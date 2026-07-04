"""Post-submit processing hold: the next turn's prep window opens only when
the submitted turn's transcript reaches a terminal status — or at the
PROCESSING_HOLD_MAX fallback, so a lost task can never freeze a debate."""
from datetime import timedelta

import pytest

from backend import config
from backend import state as S
from backend.audio import ffmpeg_available
from backend.store import get_store
from backend.transcribe import _write_transcript

from .conftest import make_tone
from .test_turn_flow import _json, _room_in_debate, _submit

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")


def _held_room(client, monkeypatch):
    """Room in turn_b1 with the prep window held on turn_a1's transcript."""
    monkeypatch.setattr(config, "TRANSCRIBE_ENABLED", True)
    monkeypatch.setattr("backend.tasks.enqueue_transcription", lambda c, t: None)
    code, tok_a, tok_b = _room_in_debate(client)
    view = _json(_submit(client, code, tok_a, make_tone(3.0, "webm"),
                         "audio/webm", "t.webm"))
    return code, tok_a, tok_b, view


def test_submit_holds_next_turn_until_transcript(client, monkeypatch):
    code, _, tok_b, view = _held_room(client, monkeypatch)
    assert view["state"] == "turn_b1"
    assert view["processing"] is True
    assert view["turn_prep_deadline_at"] is None
    assert view["turn_deadline_at"] is None

    # The mic cannot start the speaking clock while the hold is on.
    res = client.post(f"/api/rooms/{code}/turns/start",
                      headers={"X-Debater-Token": tok_b})
    assert res.status_code == 409
    assert res.get_json()["error"] == "processing"

    # Transcript lands -> the prep window opens on the same write.
    _write_transcript(code, "turn_a1", {"status": "ok", "segments": []})
    view = _json(client.get(f"/api/rooms/{code}"))
    assert view["processing"] is False
    assert view["turn_prep_deadline_at"] is not None


def test_failed_transcript_also_releases_hold(client, monkeypatch):
    code, _, _, view = _held_room(client, monkeypatch)
    assert view["processing"] is True
    _write_transcript(code, "turn_a1",
                      {"status": "failed", "segments": [], "error": "x"})
    view = _json(client.get(f"/api/rooms/{code}"))
    assert view["processing"] is False
    assert view["turn_prep_deadline_at"] is not None


def test_hold_cap_releases_without_transcript(client, monkeypatch):
    code, _, _, view = _held_room(client, monkeypatch)
    assert view["processing"] is True

    def backdate(r):
        r["processing_since"] = S.now_utc() - timedelta(
            seconds=config.PROCESSING_HOLD_MAX_SECONDS + 5)

    get_store().update(code, backdate)
    view = _json(client.get(f"/api/rooms/{code}"))  # reconcile applies the cap
    assert view["processing"] is False
    assert view["turn_prep_deadline_at"] is not None
    # The hold itself never forfeits anything.
    assert view["state"] == "turn_b1"
    assert not any(t["forfeited"] for t in view["turns"])


def test_no_hold_when_transcription_disabled(client):
    # config.TRANSCRIBE_ENABLED is False in tests by default: prep starts
    # immediately, exactly the pre-hold behavior.
    code, tok_a, _ = _room_in_debate(client)
    view = _json(_submit(client, code, tok_a, make_tone(3.0, "webm"),
                         "audio/webm", "t.webm"))
    assert view["state"] == "turn_b1"
    assert view["processing"] is False
    assert view["turn_prep_deadline_at"] is not None
