"""The speech gate + anti-fabrication + coverage fixes (the EY52EC forensics).

Real incident: a dead mic produced -91 dB captures; Gemini fabricated fluent
transcripts from the silence (seeded by the topic in the prompt) and the judge
ruled on speech nobody made. These tests pin every layer of the defense.
"""
import pytest

from backend import config
from backend.audio import ffmpeg_available
from backend.store import get_store
from backend.transcribe import transcribe_turn

from .conftest import make_silence, make_tone
from .test_turn_flow import _json, _room_in_debate, _submit

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")


def test_silent_upload_rejected_at_the_gate(client):
    """Layer 1: a dead-mic capture never reaches storage, Gemini, or the judge."""
    code, tok_a, _ = _room_in_debate(client)
    res = _submit(client, code, tok_a, make_silence(5.0), "audio/webm", "t.webm")
    assert res.status_code == 400
    assert res.get_json()["error"] == "silent_audio"
    # The turn was NOT consumed — the debater can fix their mic and re-record.
    view = _json(client.get(f"/api/rooms/{code}"))
    assert view["state"] == "turn_a1" and view["turns"] == []

    # A real (loud) recording still passes.
    view = _json(_submit(client, code, tok_a, make_tone(3.0, "webm"),
                         "audio/webm", "t.webm"))
    assert view["state"] == "turn_b1"


def _recorded_turn(client, monkeypatch):
    """One uploaded tone turn with transcription queued OFF (manual control)."""
    monkeypatch.setattr(config, "TRANSCRIBE_ENABLED", True)
    monkeypatch.setattr("backend.tasks.enqueue_transcription", lambda c, t: None)
    code, tok_a, _ = _room_in_debate(client)
    _json(_submit(client, code, tok_a, make_tone(3.0, "webm"), "audio/webm", "t.webm"))
    return code


def test_transcribe_refuses_silent_audio_stats(client, monkeypatch):
    """Layer 2 (belt): even if silent audio slipped past the gate, transcription
    fails it WITHOUT calling the model — fabrication is impossible."""
    code = _recorded_turn(client, monkeypatch)

    def set_silent(r):
        r["turns"][0]["audio_stats"] = {"max_db": -91.0, "mean_db": -91.0,
                                        "speech_end_s": 0.0}
    get_store().update(code, set_silent)

    def boom(*a, **kw):
        raise AssertionError("model must not be called for silent audio")
    monkeypatch.setattr("backend.transcribe.generate_json", boom)

    assert transcribe_turn(code, "turn_a1") == "failed"
    tr = get_store().get(code)["turns"][0]["transcript"]
    assert tr["error"] == "silent audio (gate)"


def test_short_coverage_triggers_continue_retry(client, monkeypatch):
    """Layer 3: transcript ending mid-speech gets one explicit continue retry."""
    code = _recorded_turn(client, monkeypatch)

    def set_stats(r):
        r["turns"][0]["duration_s"] = 60.0
        r["turns"][0]["audio_stats"] = {"max_db": -3.0, "mean_db": -20.0,
                                        "speech_end_s": 55.0}
    get_store().update(code, set_stats)

    calls = []

    def fake(prompt, schema, **kw):
        calls.append(prompt)
        end = "00:20" if len(calls) == 1 else "00:54"   # short, then complete
        return {"segments": [{"start": "00:00", "end": end, "text": "كلام"}]}
    monkeypatch.setattr("backend.transcribe.generate_json", fake)

    assert transcribe_turn(code, "turn_a1") == "ok"
    assert len(calls) == 2
    assert "حتى الثانية 55" in calls[1]            # the retry names the real speech end
    tr = get_store().get(code)["turns"][0]["transcript"]
    assert tr["segments"][-1]["end_s"] == 54.0
    assert "degraded" not in tr


def test_still_short_after_retry_is_flagged_degraded(client, monkeypatch):
    code = _recorded_turn(client, monkeypatch)

    def set_stats(r):
        r["turns"][0]["duration_s"] = 60.0
        r["turns"][0]["audio_stats"] = {"max_db": -3.0, "mean_db": -20.0,
                                        "speech_end_s": 55.0}
    get_store().update(code, set_stats)

    monkeypatch.setattr(
        "backend.transcribe.generate_json",
        lambda *a, **kw: {"segments": [{"start": "00:00", "end": "00:20", "text": "كلام"}]})

    assert transcribe_turn(code, "turn_a1") == "ok"   # partial truth beats none
    tr = get_store().get(code)["turns"][0]["transcript"]
    assert tr["degraded"] == "tail_missing"
