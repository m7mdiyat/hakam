"""Transcription unit tests: timestamp parsing, segment validation, and the
turn-upload -> queued transcription -> stored segments flow with Gemini mocked."""
import io

import pytest

from backend import config
from backend.transcribe import SegmentError, normalize_segments, parse_ts

from .conftest import make_tone
from .test_turn_flow import _json, _room_in_debate, _submit


# --- parse_ts ----------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("00:00", 0.0), ("0:07", 7.0), ("01:30", 90.0), ("1:02:03", 3723.0),
    ("00:05.5", 5.5), (" 02:10 ", 130.0),
    # Python's \d and int() accept Arabic-Indic digits — deliberate robustness
    # if the model ever ignores the Western-digits instruction.
    ("١٢:٣٤", 754.0),
])
def test_parse_ts_valid(raw, expected):
    assert parse_ts(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["", "abc", "90", "1:2", None])
def test_parse_ts_invalid(raw):
    assert parse_ts(raw) is None


# --- normalize_segments ------------------------------------------------------
def _seg(start, end, text="كلام"):
    return {"start": start, "end": end, "text": text}


def test_segments_happy_path():
    out = normalize_segments(
        [_seg("00:00", "00:06"), _seg("00:06", "00:11", "ولذلك أرى أن")],
        duration_s=12.0,
    )
    assert [s["i"] for s in out] == [0, 1]
    assert out[1] == {"i": 1, "start_s": 6.0, "end_s": 11.0, "text": "ولذلك أرى أن"}


def test_segments_sorted_and_clamped():
    out = normalize_segments(
        [_seg("00:08", "00:20"), _seg("00:00", "00:07")], duration_s=10.0
    )
    assert out[0]["start_s"] == 0.0
    assert out[1]["end_s"] == 10.0  # clamped to real duration


def test_segments_swapped_pair_salvaged():
    out = normalize_segments([_seg("00:09", "00:02")], duration_s=10.0)
    assert (out[0]["start_s"], out[0]["end_s"]) == (2.0, 9.0)


def test_segments_beyond_duration_rejected():
    with pytest.raises(SegmentError):
        normalize_segments([_seg("03:30", "03:40")], duration_s=10.0)


def test_segments_unparseable_rejected():
    with pytest.raises(SegmentError):
        normalize_segments([_seg("xx", "00:05")], duration_s=10.0)


def test_segments_empty_rejected():
    with pytest.raises(SegmentError):
        normalize_segments([_seg("00:00", "00:05", "  ")], duration_s=10.0)


# --- end-to-end flow with the model mocked ----------------------------------
FAKE_SEGMENTS = {"segments": [
    {"start": "00:00", "end": "00:02", "text": "أرى أن التعليم عن بعد أفضل"},
    {"start": "00:02", "end": "00:03", "text": "وسأثبت ذلك"},
]}


def test_turn_upload_triggers_queued_transcription(client, monkeypatch):
    calls = {}

    def fake_generate_json(prompt, schema, parts=None, **kw):
        calls["prompt"] = prompt
        calls["n_parts"] = len(parts or [])
        return FAKE_SEGMENTS

    from backend.transcribe import transcribe_turn

    monkeypatch.setattr(config, "TRANSCRIBE_ENABLED", True)
    monkeypatch.setattr("backend.transcribe.generate_json", fake_generate_json)
    # Run the "queue" inline so the test is deterministic (no thread timing).
    # rooms.py does `from .tasks import enqueue_transcription` at call time,
    # so patching the tasks module is sufficient.
    monkeypatch.setattr("backend.tasks.enqueue_transcription", transcribe_turn)

    code, token_a, _ = _room_in_debate(client)
    view = _json(_submit(client, code, token_a, make_tone(3.0, "webm"),
                         "audio/webm", "turn.webm"))
    assert view["state"] == "turn_b1"

    room = _json(client.get(f"/api/rooms/{code}"))
    tr = room["turns"][0]["transcript"]
    assert tr["status"] == "ok"
    assert [s["text"] for s in tr["segments"]] == [
        "أرى أن التعليم عن بعد أفضل", "وسأثبت ذلك"]
    assert tr["segments"][0] == {"i": 0, "start_s": 0.0, "end_s": 2.0,
                                 "text": "أرى أن التعليم عن بعد أفضل"}
    assert calls["n_parts"] == 1                      # the audio part
    assert "موضوع الاختبار" in calls["prompt"]          # topic reached the prompt


def test_transcription_failure_marks_failed_not_crashing(client, monkeypatch):
    from backend.gemini import GeminiError

    def boom(*a, **kw):
        raise GeminiError("model unavailable")

    from backend.transcribe import transcribe_turn

    monkeypatch.setattr(config, "TRANSCRIBE_ENABLED", True)
    monkeypatch.setattr("backend.transcribe.generate_json", boom)
    monkeypatch.setattr("backend.tasks.enqueue_transcription", transcribe_turn)

    code, token_a, _ = _room_in_debate(client)
    _json(_submit(client, code, token_a, make_tone(3.0, "webm"),
                  "audio/webm", "turn.webm"))
    tr = _json(client.get(f"/api/rooms/{code}"))["turns"][0]["transcript"]
    assert tr["status"] == "failed"
    assert tr["attempts"] >= 1


def test_internal_endpoint_requires_oidc(client):
    res = client.post("/api/internal/transcribe", json={"code": "ABC123", "turn": "turn_a1"})
    assert res.status_code == 403
