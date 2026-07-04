"""End-to-end turn upload through the Flask client (local store + local audio):
create -> join -> claims -> ready -> submit turn_a1 -> fetch its audio.
Verifies the 2a pipeline wiring: transcode, real duration, m4a serving."""
import io

import pytest

from backend import config
from backend.audio import ffmpeg_available

from .conftest import make_tone

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")


def _json(res):
    assert res.status_code < 500, res.get_data(as_text=True)
    return res.get_json()


def _room_in_debate(client):
    """Drive a fresh room to state turn_a1; returns (code, token_a, token_b)."""
    created = _json(client.post("/api/rooms", json={"topic": "موضوع الاختبار"}))
    code, token_a = created["code"], created["token"]

    joined = _json(client.post(f"/api/rooms/{code}/join", json={
        "name": "سارة", "claim": "الدعوى الثانية", "consent": True,
    }))
    token_b = joined["token"]

    client.post(f"/api/rooms/{code}/claim", json={"name": "أحمد", "claim": "الدعوى الأولى"},
                headers={"X-Debater-Token": token_a})
    client.post(f"/api/rooms/{code}/ready", json={"ready": True},
                headers={"X-Debater-Token": token_a})
    state = _json(client.post(f"/api/rooms/{code}/ready", json={"ready": True},
                              headers={"X-Debater-Token": token_b}))
    assert state["state"] == "turn_a1"
    return code, token_a, token_b


def _submit(client, code, token, tone_bytes, mime, filename):
    return client.post(
        f"/api/rooms/{code}/turns",
        data={"audio": (io.BytesIO(tone_bytes), filename, mime), "duration_ms": "3000"},
        headers={"X-Debater-Token": token},
        content_type="multipart/form-data",
    )


def test_turn_upload_transcodes_and_serves_m4a(client):
    code, token_a, token_b = _room_in_debate(client)

    view = _json(_submit(client, code, token_a, make_tone(3.0, "webm"),
                         "audio/webm", "turn.webm"))
    assert view["state"] == "turn_b1"
    (turn,) = view["turns"]
    assert turn["turn"] == "turn_a1" and turn["has_audio"]
    # Authoritative ffprobe duration, not the client-reported one.
    assert turn["duration_s"] == pytest.approx(3.0, abs=0.35)
    assert turn["duration_ms"] == pytest.approx(3000, abs=350)

    # Playback prefers the canonical m4a whichever device recorded (webm in).
    res = client.get(f"/api/rooms/{code}/turns/turn_a1/audio",
                     headers={"X-Debater-Token": token_b})
    assert res.status_code == 200
    assert res.mimetype == "audio/mp4"
    assert res.data[4:8] == b"ftyp"


def test_overlong_audio_trimmed_to_turn_cap(client):
    code, token_a, _ = _room_in_debate(client)
    # 135s > TURN_SECONDS(120) + grace(10): the byte cap can't bound time, the
    # transcode trim does. A take that ran long (throttled tab, late auto-stop)
    # keeps its legitimate window instead of losing the whole turn.
    view = _json(_submit(client, code, token_a, make_tone(135.0, "webm"),
                         "audio/webm", "turn.webm"))
    assert view["state"] == "turn_b1"
    (turn,) = view["turns"]
    cap = config.TURN_SECONDS + config.AUDIO_DURATION_GRACE_SECONDS
    assert turn["duration_s"] == pytest.approx(cap, abs=0.5)


def test_unreadable_audio_rejected(client):
    code, token_a, _ = _room_in_debate(client)
    res = _submit(client, code, token_a, b"x" * 2048, "audio/webm", "turn.webm")
    assert res.status_code == 400
    assert res.get_json()["error"] == "bad_audio"


def _assert_firestore_safe(value, path="room"):
    # Firestore rejects an array nested directly inside an array («Property
    # array contains an invalid nested entity») — the local JSON/memory store
    # doesn't, so without this walk a bad shape only explodes in production.
    if isinstance(value, dict):
        for k, v in value.items():
            _assert_firestore_safe(v, f"{path}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            assert not isinstance(v, (list, tuple)), \
                f"nested array at {path}[{i}] — Firestore would reject this doc"
            _assert_firestore_safe(v, f"{path}[{i}]")


def test_room_doc_stays_firestore_safe_after_submit(client):
    from backend.store import get_store

    from .conftest import make_gapped_tone

    code, token_a, _ = _room_in_debate(client)
    # A take with a real pause populates audio_stats.silences — the field that
    # once shipped as [[start, end], ...] and broke every production submit.
    view = _json(_submit(client, code, token_a, make_gapped_tone(2.0, 1.0, 2.0),
                         "audio/webm", "turn.webm"))
    assert view["state"] == "turn_b1"
    room = get_store().get(code)
    assert room["turns"][0]["audio_stats"]["silences"]  # the pause was measured
    _assert_firestore_safe(room)
