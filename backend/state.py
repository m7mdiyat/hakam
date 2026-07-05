"""Room domain model + state machine.

Pure functions over a plain room dict. The server is the single source of truth for
whose turn it is and for timer deadlines; clients only render. Time fields are
timezone-aware UTC datetimes in-memory; the store layer persists them and the API
layer serializes them to ISO-8601.

State machine:
    lobby -> claims -> turn_a1 -> turn_b1 -> turn_a2 -> turn_b2 -> deliberating
    plus: abandoned (no activity for ABANDON_MINUTES)
`state` equals the current turn key while a turn is active, so it doubles as the
"whose turn" pointer alongside turn_index (state == turn_order[turn_index]).
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

from . import config

# --- lifecycle states -------------------------------------------------------
LOBBY = "lobby"          # created; waiting for debater B to join
CLAIMS = "claims"        # both joined; setting claims + readying up
DELIBERATING = "deliberating"  # all turns done or both finished -> Phase 2 judges here
ABANDONED = "abandoned"
PRE_DEBATE = (LOBBY, CLAIMS)
TERMINAL = (DELIBERATING, ABANDONED)

SIDES = ("a", "b")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def build_turn_order(rounds_per_side: int) -> list[str]:
    """[a1, b1, a2, b2, ...] — A opens each round, then B."""
    order: list[str] = []
    for r in range(1, rounds_per_side + 1):
        order.append(f"turn_a{r}")
        order.append(f"turn_b{r}")
    return order


def side_of_turn(turn_key: str) -> str:
    # "turn_a1" -> "a"
    return turn_key.split("_")[1][0]


def is_turn_state(state: str) -> bool:
    return state.startswith("turn_")


# --- construction -----------------------------------------------------------
def new_room(code: str, topic: str, token_a: str) -> dict:
    now = now_utc()
    order = build_turn_order(config.ROUNDS_PER_SIDE)
    return {
        "code": code,
        "topic": (topic or "").strip(),
        "state": LOBBY,
        "format": {
            "rounds_per_side": config.ROUNDS_PER_SIDE,
            "turn_seconds": config.TURN_SECONDS,
        },
        "debaters": {
            # A is the creator; consent is implicit by creating a recorded session.
            "a": {"name": None, "claim": None, "ready": False, "consent": True,
                  "joined_at": now, "last_seen_at": now},
            "b": {"name": None, "claim": None, "ready": False, "consent": False,
                  "joined_at": None, "last_seen_at": None},
        },
        "turn_order": order,
        "turn_index": 0,
        "turn_deadline_at": None,       # speaking clock; set when the mic starts
        "turn_prep_deadline_at": None,  # start-your-mic-by; set when a turn begins
        "processing_since": None,       # both clocks off: waiting on a transcript
        "turns": [],
        "finish_requested": {"a": False, "b": False},
        "verdict": None,  # Phase 2
        "spectators": {},  # token -> {name, joined_at, last_seen_at}; read-only viewers
        "secret_tokens": {"a": token_a, "b": None},
        "created_at": now,
        "updated_at": now,
        "last_activity_at": now,
        "expires_at": now + timedelta(hours=config.ROOM_TTL_HOURS),
    }


# --- helpers ----------------------------------------------------------------
def side_of_token(room: dict, token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    for s in SIDES:
        if room["secret_tokens"].get(s) == token:
            return s
    return None


def current_turn(room: dict) -> Optional[str]:
    if is_turn_state(room["state"]):
        return room["turn_order"][room["turn_index"]]
    return None


def has_submission(room: dict, turn_key: str) -> bool:
    return any(t["turn"] == turn_key for t in room["turns"])


def both_joined(room: dict) -> bool:
    return bool(room["debaters"]["a"]["name"]) and bool(room["debaters"]["b"]["name"])


def claim_set(room: dict, side: str) -> bool:
    d = room["debaters"][side]
    return bool(d["name"]) and bool(d["claim"])


def both_ready(room: dict) -> bool:
    return (
        claim_set(room, "a") and claim_set(room, "b")
        and room["debaters"]["a"]["ready"] and room["debaters"]["b"]["ready"]
    )


def _touch(room: dict, now: datetime, activity: bool = False) -> None:
    room["updated_at"] = now
    if activity:
        room["last_activity_at"] = now


def presence_stale(room: dict, side: str, now: Optional[datetime] = None) -> bool:
    """True when this debater's last_seen is old enough to warrant a write."""
    now = now or now_utc()
    seen = room["debaters"][side].get("last_seen_at")
    return seen is None or (now - seen).total_seconds() > config.PRESENCE_BUMP_SECONDS


def bump_presence(room: dict, side: str, now: Optional[datetime] = None) -> None:
    room["debaters"][side]["last_seen_at"] = now or now_utc()


# --- spectators (named read-only viewers) ------------------------------------
def spectator_of_token(room: dict, token: Optional[str]) -> bool:
    return bool(token) and token in (room.get("spectators") or {})


def add_spectator(room: dict, token: str, name: str, now: Optional[datetime] = None) -> None:
    """Seat a named spectator. Caller enforces the cap. Does NOT bump
    last_activity_at — viewers must not keep an idle debate from abandoning."""
    now = now or now_utc()
    room.setdefault("spectators", {})[token] = {
        "name": name, "joined_at": now, "last_seen_at": now,
    }
    _touch(room, now)


def spectator_presence_stale(room: dict, token: str, now: Optional[datetime] = None) -> bool:
    now = now or now_utc()
    entry = (room.get("spectators") or {}).get(token)
    if entry is None:
        return False
    seen = entry.get("last_seen_at")
    return seen is None or (now - seen).total_seconds() > config.SPECTATOR_PRESENCE_BUMP_SECONDS


def bump_spectator_presence(room: dict, token: str, now: Optional[datetime] = None) -> None:
    entry = (room.get("spectators") or {}).get(token)
    if entry is not None:
        entry["last_seen_at"] = now or now_utc()


def is_expired(room: dict, now: Optional[datetime] = None) -> bool:
    now = now or now_utc()
    return now > room["expires_at"]


# --- transitions (each returns True if it mutated) --------------------------
def set_format(room: dict, rounds_per_side: int, now: Optional[datetime] = None) -> None:
    """Creator adjusts the round count in the lobby. Resets BOTH ready flags so
    nobody is committed to a format they didn't see. Caller has validated
    pre-debate state and the allowed range."""
    now = now or now_utc()
    room["format"]["rounds_per_side"] = int(rounds_per_side)
    room["turn_order"] = build_turn_order(int(rounds_per_side))
    for s in SIDES:
        room["debaters"][s]["ready"] = False
    _touch(room, now, activity=True)


def set_topic(room: dict, topic: str, now: Optional[datetime] = None) -> None:
    """Creator rewords the debate topic in the lobby. Resets BOTH ready flags —
    a claim written for one topic must not auto-carry consent to another.
    Caller has validated pre-debate state and the text."""
    now = now or now_utc()
    room["topic"] = topic
    for s in SIDES:
        room["debaters"][s]["ready"] = False
    _touch(room, now, activity=True)


def is_processing(room: dict) -> bool:
    """Between turns: the previous turn's transcript is still being produced,
    so the next turn's prep window hasn't opened and no clock runs."""
    return (is_turn_state(room["state"])
            and room.get("processing_since") is not None
            and room["turn_deadline_at"] is None
            and room.get("turn_prep_deadline_at") is None)


def rematch_room(old: dict, new_code: str, now: Optional[datetime] = None) -> dict:
    """Fresh room for the SAME two debaters (verdict-screen rematch): topic,
    format, names, claims and tokens carry over; turns/verdict/clocks are fresh.
    Tokens are reused deliberately — same two holders, same capability
    boundary — so the opponent's client can follow the old room's rematch_code
    without any secret-delivery channel. Both sides must re-ready before
    anything records; that is the re-consent gate."""
    now = now or now_utc()
    room = new_room(new_code, old["topic"], old["secret_tokens"]["a"])
    room["format"] = dict(old["format"])
    room["turn_order"] = build_turn_order(old["format"]["rounds_per_side"])
    room["secret_tokens"]["b"] = old["secret_tokens"]["b"]
    for s in SIDES:
        d = old["debaters"][s]
        room["debaters"][s].update({
            "name": d["name"], "claim": d["claim"], "consent": d["consent"],
            "joined_at": now, "last_seen_at": None,   # presence unknown = online
        })
    room["state"] = CLAIMS
    return room


def _begin_turn_prep(room: dict, now: datetime) -> None:
    """A turn just became active: the speaking clock does NOT run yet — the
    debater gets a prep window to tap the mic (turns/start)."""
    room["turn_deadline_at"] = None
    room["turn_prep_deadline_at"] = now + timedelta(seconds=config.PREP_SECONDS)
    room["processing_since"] = None


def start_debate(room: dict, now: Optional[datetime] = None) -> None:
    now = now or now_utc()
    room["state"] = room["turn_order"][0]
    room["turn_index"] = 0
    _begin_turn_prep(room, now)
    _touch(room, now, activity=True)


def advance_turn(room: dict, now: Optional[datetime] = None) -> None:
    now = now or now_utc()
    room["turn_index"] += 1
    if room["turn_index"] >= len(room["turn_order"]):
        room["state"] = DELIBERATING
        room["turn_deadline_at"] = None
        room["turn_prep_deadline_at"] = None
    else:
        room["state"] = room["turn_order"][room["turn_index"]]
        _begin_turn_prep(room, now)
    _touch(room, now)


def start_turn(room: dict, now: Optional[datetime] = None) -> None:
    """The debater tapped the mic: the speaking clock starts NOW (server-stamped).
    Idempotent — a second tap / duplicate request changes nothing."""
    now = now or now_utc()
    if room["turn_deadline_at"] is not None:
        return
    room["turn_deadline_at"] = now + timedelta(seconds=room["format"]["turn_seconds"])
    room["turn_prep_deadline_at"] = None
    _touch(room, now, activity=True)


def record_turn(room: dict, side: str, audio_uri: str, duration_ms: int,
                content_type: str, m4a_uri: Optional[str] = None,
                duration_s: Optional[float] = None,
                audio_stats: Optional[dict] = None,
                transcribe_pending: bool = False,
                now: Optional[datetime] = None) -> None:
    """Append a real (recorded) turn for `side` and advance. Caller has validated turn.

    `m4a_uri`/`duration_s` come from the upload-time ffmpeg transcode: the canonical
    mono-AAC rendition (what clients play and Gemini reads) and its authoritative
    duration. None only when ffmpeg is unavailable (bare local dev).
    `transcribe_pending` marks the turn as awaiting the transcription worker
    (the caller enqueues it after this update commits)."""
    now = now or now_utc()
    turn_key = current_turn(room)
    room["turns"].append({
        "turn": turn_key,
        "debater": side,
        "audio_uri": audio_uri,
        "audio_m4a_uri": m4a_uri,
        "content_type": content_type,
        "duration_ms": int(duration_ms),
        "duration_s": duration_s,
        "audio_stats": audio_stats,  # {max_db, mean_db, speech_end_s} from upload
        "forfeited": False,
        "transcript": {"status": "pending", "segments": [], "attempts": 0}
        if transcribe_pending else None,
        "created_at": now,
    })
    _touch(room, now, activity=True)
    advance_turn(room, now)
    if transcribe_pending and is_turn_state(room["state"]):
        # Processing hold: the next turn's prep window opens when this turn's
        # transcript lands (release_processing_hold) or at the reconcile cap —
        # the opponent replies to a transcribed turn, and no clock runs
        # against anyone while the transcription worker is busy.
        room["turn_prep_deadline_at"] = None
        room["processing_since"] = now


def release_processing_hold(room: dict, turn_key: str, now: Optional[datetime] = None) -> None:
    """The transcript for `turn_key` reached a terminal status (ok or failed —
    the wait is over either way): if the next turn's prep is held on it, open
    the prep window now. The hold is only ever on the LAST recorded turn."""
    if not is_processing(room):
        return
    if not room["turns"] or room["turns"][-1]["turn"] != turn_key:
        return
    now = now or now_utc()
    _begin_turn_prep(room, now)
    _touch(room, now)


def _forfeit_current_turn(room: dict, now: datetime) -> None:
    """Auto-advance a turn whose deadline passed with no recording (no-show)."""
    turn_key = current_turn(room)
    room["turns"].append({
        "turn": turn_key,
        "debater": side_of_turn(turn_key),
        "audio_uri": None,
        "audio_m4a_uri": None,
        "duration_ms": 0,
        "duration_s": None,
        "forfeited": True,
        "transcript": None,
        "created_at": now,
    })
    # NOTE: forfeit is a system action -> does NOT bump last_activity_at, so an
    # abandoned room still eventually flips to ABANDONED.
    advance_turn(room, now)


# --- judging (Phase 2) -------------------------------------------------------
def begin_judging(room: dict, now: Optional[datetime] = None) -> bool:
    """Claim the judging lease. True iff this caller should run the pipeline.
    Reclaimable when a previous run failed or its lease went stale (crashed
    request / dropped client)."""
    now = now or now_utc()
    if room["state"] != DELIBERATING or room.get("verdict"):
        return False
    j = room.get("judging") or {}
    if j.get("status") == "done":
        return False
    if j.get("status") == "running" and j.get("lease_at") is not None:
        if (now - j["lease_at"]).total_seconds() < config.JUDGE_LEASE_SECONDS:
            return False
    room["judging"] = {"status": "running", "lease_at": now,
                       "attempts": int(j.get("attempts", 0)) + 1, "error": None}
    _touch(room, now)
    return True


def finish_judging(room: dict, verdict: dict, now: Optional[datetime] = None) -> None:
    now = now or now_utc()
    room["verdict"] = verdict
    room["judging"] = {**(room.get("judging") or {}), "status": "done",
                       "lease_at": None, "error": None}
    _touch(room, now, activity=True)


def fail_judging(room: dict, error: str, now: Optional[datetime] = None) -> None:
    now = now or now_utc()
    room["judging"] = {**(room.get("judging") or {}), "status": "failed",
                       "lease_at": None, "error": error}
    _touch(room, now)


def request_finish(room: dict, side: str, now: Optional[datetime] = None) -> None:
    now = now or now_utc()
    room["finish_requested"][side] = True
    _touch(room, now, activity=True)
    if room["finish_requested"]["a"] and room["finish_requested"]["b"]:
        room["state"] = DELIBERATING
        room["turn_deadline_at"] = None


# --- reconcile: the lazy, server-authoritative timer ------------------------
def reconcile(room: dict, now: Optional[datetime] = None) -> bool:
    """Apply time-driven transitions. Called on every read and mutation.

    - No-show: an active turn past deadline + NOSHOW_GRACE with no upload is
      forfeited and advanced (looped, to catch several missed turns at once).
    - Abandonment: a non-terminal room idle for ABANDON_MINUTES -> abandoned.

    Returns True if the room changed (so the store knows to persist).
    """
    now = now or now_utc()
    changed = False

    # No-show forfeits (bounded loop). Two clocks can expire a turn: the prep
    # window (never tapped the mic) or the speaking deadline (started, never
    # submitted). A STARTED turn only forfeits after the submit window has
    # fully closed (deadline + SUBMIT_GRACE): a debater who spoke to the buzzer
    # necessarily uploads after the deadline, and this reconcile runs on every
    # poll from EITHER side — forfeiting inside the submit window would kill a
    # legitimate upload mid-flight.
    for _ in range(len(room["turn_order"]) + 1):
        if not is_turn_state(room["state"]):
            break
        if room["turn_deadline_at"]:
            deadline = room["turn_deadline_at"] + timedelta(
                seconds=config.SUBMIT_GRACE_SECONDS)
        elif room["turn_prep_deadline_at"]:
            deadline = room["turn_prep_deadline_at"]
        elif room.get("processing_since") is not None:
            # Processing hold: no clock runs (nothing can forfeit), but a
            # lost/slow transcription task must not freeze the debate — past
            # the cap, the held turn's prep window opens anyway.
            if now > room["processing_since"] + timedelta(
                    seconds=config.PROCESSING_HOLD_MAX_SECONDS):
                _begin_turn_prep(room, now)
                changed = True
            break
        else:
            break
        overdue = now > deadline + timedelta(seconds=config.NOSHOW_GRACE_SECONDS)
        if overdue and not has_submission(room, current_turn(room)):
            _forfeit_current_turn(room, now)
            changed = True
        else:
            break

    # Abandonment (pre-debate idling or a fully-stalled live debate).
    if room["state"] in PRE_DEBATE or is_turn_state(room["state"]):
        idle = now - room["last_activity_at"]
        if idle > timedelta(minutes=config.ABANDON_MINUTES):
            room["state"] = ABANDONED
            room["turn_deadline_at"] = None
            changed = True

    if changed:
        room["updated_at"] = now
    return changed


# --- public projection ------------------------------------------------------
def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def public_view(room: dict, now: Optional[datetime] = None) -> dict:
    """Whitelisted, secret-free JSON view. Includes server_now for clock-skew-safe
    countdowns: the client renders remaining = turn_deadline_at - server_now."""
    now = now or now_utc()

    def debater(side: str) -> dict:
        d = room["debaters"][side]
        seen = d.get("last_seen_at")
        return {
            "side": side,
            "name": d["name"],
            "claim": d["claim"],
            "ready": d["ready"],
            "consent": d["consent"],
            "joined": bool(d["name"]),
            # None = never seen polling (unknown); the UI treats unknown as online.
            "online": (None if seen is None else
                       (now - seen).total_seconds() < config.PRESENCE_TTL_SECONDS),
        }

    # Names + liveness only — spectator tokens never leave the doc.
    spectators = [{
        "name": s["name"],
        "online": (None if s.get("last_seen_at") is None else
                   (now - s["last_seen_at"]).total_seconds()
                   < config.SPECTATOR_PRESENCE_TTL_SECONDS),
    } for s in sorted((room.get("spectators") or {}).values(),
                      key=lambda s: s["joined_at"])]

    turns = [{
        "turn": t["turn"],
        "debater": t["debater"],
        "duration_ms": t.get("duration_ms", 0),
        "duration_s": t.get("duration_s"),
        "forfeited": t.get("forfeited", False),
        "has_audio": bool(t.get("audio_uri")),
        "transcript": t.get("transcript"),  # null in Phase 1
        "created_at": _iso(t.get("created_at")),
    } for t in room["turns"]]

    return {
        "code": room["code"],
        "topic": room["topic"],
        "state": room["state"],
        "format": room["format"],
        "debaters": {"a": debater("a"), "b": debater("b")},
        "turn_order": room["turn_order"],
        "turn_index": room["turn_index"],
        "current_turn": current_turn(room),
        "turn_deadline_at": _iso(room["turn_deadline_at"]),
        "turn_prep_deadline_at": _iso(room.get("turn_prep_deadline_at")),
        "turn_started": room["turn_deadline_at"] is not None,
        "processing": is_processing(room),
        "turns": turns,
        "finish_requested": room["finish_requested"],
        "spectators": spectators,
        "both_ready": both_ready(room),
        "judging_status": (room.get("judging") or {}).get("status"),
        "verdict": room["verdict"],
        # Set once the creator starts a rematch; the opponent's poll follows it.
        "rematch_code": (room.get("rematch") or {}).get("code"),
        "expires_at": _iso(room["expires_at"]),
        "server_now": _iso(now),
    }
