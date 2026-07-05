"""Hakam API (Phase 1). All routes under /api.

Auth: each debater sends their capability token as `X-Debater-Token`. The server is
the single source of truth for whose turn it is and for timer deadlines — every turn
submission is re-validated inside the atomic store update, so two racing clients (or a
client with a skewed clock) cannot submit out of turn or past the deadline.
"""
from __future__ import annotations

from datetime import timedelta

from flask import Blueprint, Response, jsonify, request

from . import config
from . import state as S
from .codes import gen_code, gen_token, normalize_code
from .store import AlreadyExists, NotFound, get_store

api = Blueprint("api", __name__, url_prefix="/api")

TOPIC_MAX = 300
NAME_MAX = 40
CLAIM_MAX = 400


# --- error type rendered as JSON -------------------------------------------
class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@api.errorhandler(ApiError)
def _handle_api_error(e: ApiError):
    return jsonify({"error": e.code, "message": e.message}), e.status


# --- helpers ----------------------------------------------------------------
def _token() -> str:
    return request.headers.get("X-Debater-Token", "").strip()


def _client_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _clean(value, maxlen: int, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ApiError(400, "invalid_input", f"الحقل «{field}» مطلوب.")
    v = value.strip()
    if len(v) > maxlen:
        raise ApiError(400, "invalid_input", f"الحقل «{field}» أطول من المسموح.")
    return v


def _load(code: str) -> dict:
    """Reconciled room or raise. Applies lazy timers (no-show / abandon / expiry)."""
    code = normalize_code(code)
    room = get_store().get_reconciled(code)
    if room is None:
        raise ApiError(404, "not_found", "الجلسة غير موجودة.")
    if S.is_expired(room):
        raise ApiError(410, "expired", "انتهت صلاحية الجلسة.")
    return room


def _require_side(room: dict) -> str:
    side = S.side_of_token(room, _token())
    if side is None:
        raise ApiError(401, "unauthorized", "رمز غير صالح لهذه الجلسة.")
    return side


def _view(room: dict):
    return jsonify(S.public_view(room))


# --- endpoints --------------------------------------------------------------
@api.post("/rooms")
def create_room():
    if not get_store().rate_check(
        f"create:{_client_ip()}", config.CREATE_RATE_LIMIT, config.CREATE_RATE_WINDOW_SECONDS
    ):
        raise ApiError(429, "rate_limited", "محاولات كثيرة. حاول بعد قليل.")

    topic = _clean(_body().get("topic"), TOPIC_MAX, "الموضوع")
    token_a = gen_token()

    # Generate a unique code (retry on the rare collision).
    for _ in range(8):
        code = gen_code()
        room = S.new_room(code, topic, token_a)
        try:
            get_store().create(room)
            return jsonify({"code": code, "token": token_a, "side": "a"}), 201
        except AlreadyExists:
            continue
    raise ApiError(503, "code_exhausted", "تعذّر إنشاء رمز. حاول مجددًا.")


@api.post("/rooms/<code>/join")
def join_room(code):
    name = _clean(_body().get("name"), NAME_MAX, "الاسم")
    claim = _clean(_body().get("claim"), CLAIM_MAX, "الدعوى")
    if _body().get("consent") is not True:
        raise ApiError(400, "consent_required", "الموافقة على التسجيل مطلوبة للانضمام.")

    _load(code)  # existence/expiry check before we mint a token
    captured = {}

    def mut(room: dict):
        if room["state"] != S.LOBBY or room["debaters"]["b"]["name"]:
            raise ApiError(409, "room_full", "اكتمل الطرفان في هذه الجلسة.")
        token_b = gen_token()
        now = S.now_utc()
        room["debaters"]["b"] = {
            "name": name, "claim": claim, "ready": False,
            "consent": True, "joined_at": now, "last_seen_at": now,
        }
        room["secret_tokens"]["b"] = token_b
        room["state"] = S.CLAIMS
        S._touch(room, now, activity=True)
        captured["token"] = token_b

    room = get_store().update(normalize_code(code), mut)
    return jsonify({"token": captured["token"], "side": "b", "room": S.public_view(room)}), 201


@api.post("/rooms/<code>/claim")
def set_claim(code):
    name = _clean(_body().get("name"), NAME_MAX, "الاسم")
    claim = _clean(_body().get("claim"), CLAIM_MAX, "الدعوى")
    room = _load(code)
    side = _require_side(room)

    def mut(r: dict):
        if r["state"] not in S.PRE_DEBATE:
            raise ApiError(409, "already_started", "بدأت المناظرة؛ لا يمكن تعديل الدعوى.")
        r["debaters"][side]["name"] = name
        r["debaters"][side]["claim"] = claim
        r["debaters"][side]["ready"] = False  # re-confirm after editing
        S._touch(r, S.now_utc(), activity=True)

    return _view(get_store().update(normalize_code(code), mut))


@api.post("/rooms/<code>/format")
def set_format(code):
    """Creator (A) adjusts the round count from the lobby, pre-debate only.
    Resets both ready flags so the format change is re-consented."""
    rounds = _body().get("rounds_per_side")
    if rounds not in config.ROUNDS_CHOICES:
        raise ApiError(400, "invalid_input", "عدد الجولات غير صالح.")
    room = _load(code)
    if _require_side(room) != "a":
        raise ApiError(403, "not_creator", "منشئ الجلسة فقط يعدّل الصيغة.")

    def mut(r: dict):
        if r["state"] not in S.PRE_DEBATE:
            raise ApiError(409, "already_started", "بدأت المناظرة؛ لا يمكن تعديل الصيغة.")
        S.set_format(r, rounds, S.now_utc())

    return _view(get_store().update(normalize_code(code), mut))


@api.post("/rooms/<code>/topic")
def set_topic(code):
    """Creator (A) rewords the debate topic from the lobby, pre-debate only.
    Resets both ready flags so the change is re-consented (like /format)."""
    topic = _clean(_body().get("topic"), TOPIC_MAX, "الموضوع")
    room = _load(code)
    if _require_side(room) != "a":
        raise ApiError(403, "not_creator", "منشئ الجلسة فقط يعدّل الموضوع.")

    def mut(r: dict):
        if r["state"] not in S.PRE_DEBATE:
            raise ApiError(409, "already_started", "بدأت المناظرة؛ لا يمكن تعديل الموضوع.")
        S.set_topic(r, topic, S.now_utc())

    return _view(get_store().update(normalize_code(code), mut))


@api.post("/rooms/<code>/turns/start")
def start_turn(code):
    """The debater tapped the mic: start the server-stamped speaking clock.
    Idempotent; only the current turn's holder may start it."""
    room = _load(code)
    side = _require_side(room)

    def mut(r: dict):
        ct = S.current_turn(r)
        if ct is None:
            raise ApiError(409, "not_active", "لا توجد جولة نشطة الآن.")
        if S.side_of_turn(ct) != side:
            raise ApiError(409, "not_your_turn", "ليس دورك الآن.")
        if S.is_processing(r):
            raise ApiError(409, "processing", "بانتظار اكتمال تدوين الجولة السابقة.")
        S.start_turn(r, S.now_utc())

    return _view(get_store().update(normalize_code(code), mut))


@api.post("/rooms/<code>/ready")
def ready(code):
    want = _body().get("ready", True)
    room = _load(code)
    side = _require_side(room)

    def mut(r: dict):
        if r["state"] not in S.PRE_DEBATE:
            raise ApiError(409, "already_started", "بدأت المناظرة بالفعل.")
        if want and not S.claim_set(r, side):
            raise ApiError(400, "claim_required", "اكتب دعواك قبل الاستعداد.")
        r["debaters"][side]["ready"] = bool(want)
        S._touch(r, S.now_utc(), activity=True)
        if S.both_ready(r):
            S.start_debate(r, S.now_utc())

    return _view(get_store().update(normalize_code(code), mut))


@api.post("/rooms/<code>/turns")
def submit_turn(code):
    room = _load(code)
    side = _require_side(room)
    now = S.now_utc()

    # Pre-checks (fast fail before reading the upload). Re-checked inside the txn.
    turn_key = S.current_turn(room)
    if turn_key is None:
        raise ApiError(409, "not_active", "لا توجد جولة نشطة الآن.")
    if S.side_of_turn(turn_key) != side:
        raise ApiError(409, "not_your_turn", "ليس دورك الآن.")
    deadline = room["turn_deadline_at"]
    if deadline and now > deadline + timedelta(seconds=config.SUBMIT_GRACE_SECONDS):
        raise ApiError(409, "turn_expired", "انتهى وقت الجولة.")

    upload = request.files.get("audio")
    if upload is None:
        raise ApiError(400, "no_audio", "لم يصل تسجيل صوتي.")
    content_type = (upload.mimetype or request.form.get("content_type") or "").lower()
    if content_type.split(";")[0].strip() not in config.ALLOWED_AUDIO_MIMES:
        raise ApiError(415, "bad_audio_type", "نوع الصوت غير مدعوم.")
    data = upload.read()
    if not data:
        raise ApiError(400, "empty_audio", "التسجيل فارغ.")
    if len(data) > config.MAX_AUDIO_BYTES:
        raise ApiError(413, "audio_too_large", "التسجيل أكبر من المسموح.")

    try:
        duration_ms = int(request.form.get("duration_ms", 0))
    except (TypeError, ValueError):
        duration_ms = 0
    duration_ms = max(0, min(duration_ms, (config.TURN_SECONDS + 5) * 1000))

    # Canonical rendition: transcode to mono m4a and measure the real duration
    # (see audio.py for why). In production ffmpeg always exists; an unreadable
    # upload is the client's problem, not a 500.
    from .audio import TranscodeError, ffmpeg_available, transcode_to_m4a
    m4a_data, duration_s, audio_stats = None, None, None
    if ffmpeg_available():
        # The byte cap can't bound time (opus bitrate varies); the transcode
        # trim can. The UI stops at TURN_SECONDS; a take that ran long anyway
        # (throttled tab, suspended phone) keeps its legitimate window instead
        # of losing the whole turn — everything past the cap is cut.
        cap_s = float(config.TURN_SECONDS + config.AUDIO_DURATION_GRACE_SECONDS)
        try:
            m4a_data, duration_s, audio_stats = transcode_to_m4a(
                data, content_type, max_duration_s=cap_s)
        except TranscodeError:
            raise ApiError(400, "bad_audio", "تعذّرت قراءة التسجيل الصوتي.")
        # SPEECH GATE: a silent capture (dead mic) is rejected before it can
        # reach storage, Gemini, or the judge — silent audio + a topic makes
        # the model fabricate a transcript, and a fabricated transcript gets
        # judged. The debater's clock keeps running; they can re-record.
        if audio_stats["max_db"] < config.SILENCE_GATE_DB:
            raise ApiError(400, "silent_audio",
                           "لم يلتقط الميكروفون أي صوت — تحقق من الميكروفون وحاول مجددًا.")
        duration_ms = int(duration_s * 1000)

    from .storage import get_storage
    audio_uri = get_storage().save(normalize_code(code), turn_key, data, content_type)
    m4a_uri = None
    if m4a_data is not None:
        m4a_uri = get_storage().save(
            normalize_code(code), turn_key, m4a_data, "audio/mp4", variant="norm"
        )

    def mut(r: dict):
        # Authoritative re-validation inside the atomic update.
        ct = S.current_turn(r)
        if ct != turn_key or S.side_of_turn(ct) != side:
            raise ApiError(409, "not_your_turn", "تغيّر الدور.")
        if r["turn_deadline_at"] and S.now_utc() > r["turn_deadline_at"] + timedelta(
            seconds=config.SUBMIT_GRACE_SECONDS
        ):
            raise ApiError(409, "turn_expired", "انتهى وقت الجولة.")
        S.record_turn(r, side, audio_uri, duration_ms, content_type,
                      m4a_uri=m4a_uri, duration_s=duration_s, audio_stats=audio_stats,
                      transcribe_pending=config.TRANSCRIBE_ENABLED, now=S.now_utc())

    room = get_store().update(normalize_code(code), mut)
    # After the turn is committed (opponent's poll already sees the new state):
    # hand transcription to the background queue — the uploader never waits on it.
    from .tasks import enqueue_transcription
    enqueue_transcription(normalize_code(code), turn_key)
    # Last turn just landed -> the room is deliberating; judge it now.
    return _view(_maybe_judge(room))


@api.post("/rooms/<code>/spectate")
def spectate(code):
    """Join as a named spectator: a read-only follow of the room in any live
    state, listed in the spectator strip for everyone. No claim, no consent —
    spectators are never recorded."""
    if not get_store().rate_check(
        f"spectate:{_client_ip()}", config.CREATE_RATE_LIMIT, config.CREATE_RATE_WINDOW_SECONDS
    ):
        raise ApiError(429, "rate_limited", "محاولات كثيرة. حاول بعد قليل.")
    name = _clean(_body().get("name"), NAME_MAX, "الاسم")
    _load(code)  # existence/expiry check before we mint a token
    token = gen_token()

    def mut(r: dict):
        if len(r.get("spectators") or {}) >= config.SPECTATOR_MAX:
            raise ApiError(409, "spectators_full", "اكتمل عدد المشاهدين لهذه الجلسة.")
        S.add_spectator(r, token, name, S.now_utc())

    room = get_store().update(normalize_code(code), mut)
    return jsonify({"token": token, "room": S.public_view(room)}), 201


@api.get("/rooms/<code>")
def get_room(code):
    """Poll target. When the poller identifies itself (token header), its
    presence is bumped — throttled so the 2s poll doesn't write Firestore
    every request. Presence powers «غير متصل» and the spectator strip."""
    room = _load(code)
    token = _token()
    side = S.side_of_token(room, token)
    if side and S.presence_stale(room, side):
        room = get_store().update(normalize_code(code),
                                  lambda r: S.bump_presence(r, side, S.now_utc()))
    elif side is None and S.spectator_of_token(room, token) \
            and S.spectator_presence_stale(room, token):
        room = get_store().update(
            normalize_code(code),
            lambda r: S.bump_spectator_presence(r, token, S.now_utc()))
    view = S.public_view(room)
    if side:
        # Live-audio signaling rides the debaters' poll only: the blobs carry
        # ICE candidates (device IPs) — never for spectators/anonymous.
        view["rtc"] = S.rtc_view(room)
    return jsonify(view)


def _maybe_judge(room: dict) -> dict:
    """Run judging inline if this room is ready for it (idempotent: the lease
    in state.begin_judging makes concurrent callers no-op). Both debaters'
    clients poll every 2s and see «الحَكَم يراجع الحجج» while this runs."""
    if (room["state"] == S.DELIBERATING and not room.get("verdict")
            and config.GEMINI_ENABLED):
        from .judge import run_judging
        return run_judging(room["code"])
    return room


@api.post("/rooms/<code>/finish")
def finish(code):
    room = _load(code)
    side = _require_side(room)

    def mut(r: dict):
        if not S.is_turn_state(r["state"]):
            raise ApiError(409, "not_active", "لا يمكن طلب الإنهاء الآن.")
        S.request_finish(r, side, S.now_utc())

    room = get_store().update(normalize_code(code), mut)
    # Second finish flag flips the room to deliberating -> judge in-request.
    return _view(_maybe_judge(room))


@api.post("/rooms/<code>/judge")
def judge_room(code):
    """Client retrigger: fired when polling shows deliberating with judging
    null/failed/stale (covers the forfeit entry path and crashed runs)."""
    room = _load(code)
    _require_side(room)
    return _view(_maybe_judge(room))


@api.post("/rooms/<code>/rtc")
def rtc_signal(code):
    """Live-audio signaling (debaters only): store my WebRTC blob — one
    vanilla-ICE offer/answer per generation, or a restart request (B can't
    offer, so it asks A to re-offer). The opponent reads it from their poll."""
    room = _load(code)
    side = _require_side(room)
    body = _body()
    try:
        gen = int(body.get("gen"))
    except (TypeError, ValueError):
        raise ApiError(400, "invalid_input", "الحقل «gen» مطلوب.")
    sdp = body.get("sdp")
    restart = bool(body.get("restart"))
    if sdp is not None:
        if (not isinstance(sdp, dict) or sdp.get("type") not in ("offer", "answer")
                or not isinstance(sdp.get("sdp"), str) or not sdp["sdp"]):
            raise ApiError(400, "invalid_input", "صيغة SDP غير صالحة.")
        if len(sdp["sdp"].encode("utf-8")) > config.RTC_MAX_SDP_BYTES:
            raise ApiError(413, "sdp_too_large", "حزمة الاتصال أكبر من المسموح.")
        sdp = {"type": sdp["type"], "sdp": sdp["sdp"]}  # whitelist keys
    elif not restart:
        raise ApiError(400, "invalid_input", "sdp أو restart مطلوب.")

    get_store().update(normalize_code(code),
                       lambda r: S.set_rtc_signal(r, side, gen, sdp, restart=restart))
    return jsonify({"ok": True})


def _fetch_turn_servers():
    """Short-lived Cloudflare TURN credentials, or None when unconfigured or
    the mint fails — callers fall back to STUN-only (live audio may not
    connect on hard NATs; the debate itself is unaffected)."""
    if not (config.TURN_KEY_ID and config.TURN_API_TOKEN):
        return None
    import json as _json
    from urllib import request as _rq
    try:
        req = _rq.Request(
            "https://rtc.live.cloudflare.com/v1/turn/keys/"
            f"{config.TURN_KEY_ID}/credentials/generate-ice-servers",
            data=_json.dumps({"ttl": config.TURN_TTL_SECONDS}).encode(),
            headers={"Authorization": f"Bearer {config.TURN_API_TOKEN}",
                     "Content-Type": "application/json"},
            method="POST")
        with _rq.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read().decode())
        servers = data.get("iceServers")
        if isinstance(servers, dict):
            servers = [servers]
        return servers if isinstance(servers, list) and servers else None
    except Exception:
        return None


@api.get("/rooms/<code>/ice")
def ice_servers(code):
    """ICE servers for the live-audio link (debaters only). STUN always;
    TURN relay credentials are minted when Cloudflare keys are configured."""
    room = _load(code)
    _require_side(room)
    servers = [{"urls": config.STUN_URLS}]
    turn = _fetch_turn_servers()
    if turn:
        servers.extend(turn)
    return jsonify({"iceServers": servers})


@api.post("/rooms/<code>/rematch")
def rematch(code):
    """Creator restarts the debate with the same opponent: a linked fresh room
    with both seats, topic, format and tokens carried over (state.rematch_room).
    The old room's public view then exposes rematch_code, so the opponent's
    polling client follows automatically — reused tokens mean no secret needs
    delivering. Idempotent: one rematch per room, later calls return it."""
    room = _load(code)
    if _require_side(room) != "a":
        raise ApiError(403, "not_creator", "منشئ الجلسة فقط يبدأ الإعادة.")
    if not room.get("verdict"):
        raise ApiError(409, "no_verdict", "الإعادة متاحة بعد صدور الحُكْم.")
    if room.get("rematch"):
        return jsonify({"code": room["rematch"]["code"]})

    for _ in range(8):
        new_code = gen_code()
        try:
            get_store().create(S.rematch_room(room, new_code))
        except AlreadyExists:
            continue

        def mut(r: dict):
            # Two racing creator clicks: first link wins; the loser's fresh
            # room is never referenced and simply expires with its TTL.
            if not r.get("rematch"):
                r["rematch"] = {"code": new_code, "at": S.now_utc()}
                S._touch(r, S.now_utc(), activity=True)

        room = get_store().update(normalize_code(code), mut)
        return jsonify({"code": room["rematch"]["code"]})
    raise ApiError(503, "code_exhausted", "تعذّر إنشاء رمز. حاول مجددًا.")


@api.post("/internal/transcribe")
def internal_transcribe():
    """Cloud Tasks worker target. Public URL, private in practice: requires the
    queue's OIDC token (audience = this service, email = the tasks SA)."""
    from .tasks import verify_task_oidc
    if not verify_task_oidc(request.headers.get("Authorization", "")):
        raise ApiError(403, "forbidden", "غير مصرّح.")
    body = _body()
    code = normalize_code(str(body.get("code", "")))
    turn_key = str(body.get("turn", ""))
    if not code or not turn_key:
        raise ApiError(400, "invalid_input", "code و turn مطلوبان.")
    from .transcribe import transcribe_turn
    status = transcribe_turn(code, turn_key)
    # 5xx tells Cloud Tasks to back off and retry; ok/skipped are terminal.
    return jsonify({"status": status}), 500 if status == "failed" else 200


@api.get("/rooms/<code>/turns/<turn>/audio")
def turn_audio(code, turn):
    # Participants and named spectators; stream the private blob through Flask.
    room = _load(code)
    if S.side_of_token(room, _token()) is None \
            and not S.spectator_of_token(room, _token()):
        raise ApiError(401, "unauthorized", "رمز غير صالح لهذه الجلسة.")
    entry = next((t for t in room["turns"] if t["turn"] == turn and t.get("audio_uri")), None)
    if entry is None:
        raise ApiError(404, "no_audio", "لا يوجد تسجيل لهذه الجولة.")
    # Prefer the canonical m4a: plays + seeks on both platforms regardless of
    # which device recorded it (webm from Android won't play on iOS Safari).
    if entry.get("audio_m4a_uri"):
        uri, mimetype = entry["audio_m4a_uri"], "audio/mp4"
    else:
        uri, mimetype = entry["audio_uri"], entry.get("content_type", "application/octet-stream")
    from .storage import get_storage
    data = get_storage().read(uri)
    resp = Response(data, mimetype=mimetype)
    resp.headers["Cache-Control"] = "private, max-age=600"
    resp.headers["Accept-Ranges"] = "none"
    return resp
