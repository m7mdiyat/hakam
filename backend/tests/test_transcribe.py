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


def test_segments_keep_model_order_and_clamp_monotonic():
    # The array order is the model's transcription order — the truth. Sorting
    # by fictional start times could scramble the token stream that quote
    # anchoring depends on, so out-of-order stamps are clamped, not sorted.
    out = normalize_segments(
        [_seg("00:08", "00:20", "الأولى"), _seg("00:00", "00:07", "الثانية")],
        duration_s=10.0,
    )
    assert [s["text"] for s in out] == ["الأولى", "الثانية"]
    assert out[0]["end_s"] == 10.0                    # clamped to real duration
    assert out[1]["start_s"] >= out[0]["start_s"]     # monotonic, never sorted


def test_segments_swapped_pair_salvaged():
    out = normalize_segments([_seg("00:09", "00:02")], duration_s=10.0)
    assert (out[0]["start_s"], out[0]["end_s"]) == (2.0, 9.0)


def test_segments_beyond_duration_survive_clamped():
    # Room PYYQWF: a flawless 2-minute transcript arrived with its tail
    # stamped past EOF (the model's bucket clock drifts fast) and the old
    # range check threw ALL of it away. Times are fiction — text survives.
    out = normalize_segments(
        [_seg("00:00", "01:55"), _seg("02:04", "02:10", "الخاتمة")],
        duration_s=118.4,
    )
    assert [s["i"] for s in out] == [0, 1]
    assert out[1]["text"] == "الخاتمة"
    assert out[1]["end_s"] <= 118.4


def test_segments_unparseable_stamp_borrows_neighbour():
    out = normalize_segments(
        [_seg("00:02", "00:05"), _seg("xx", "yy", "بلا وقت")], duration_s=10.0
    )
    assert out[1]["text"] == "بلا وقت"
    assert out[1]["start_s"] == 5.0  # borrowed from the previous end


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
    assert "reason" not in tr  # a pipeline error is NOT blamed on the mic


def test_model_empty_transcript_flags_no_speech(client, monkeypatch):
    # Room XUXX7S: loud rustling passes the amplitude gate, the model rightly
    # returns zero segments — the debater must learn it was the microphone.
    from backend.transcribe import transcribe_turn

    monkeypatch.setattr(config, "TRANSCRIBE_ENABLED", True)
    monkeypatch.setattr("backend.transcribe.generate_json",
                        lambda *a, **kw: {"segments": []})
    monkeypatch.setattr("backend.tasks.enqueue_transcription", transcribe_turn)

    code, token_a, _ = _room_in_debate(client)
    _json(_submit(client, code, token_a, make_tone(3.0, "webm"),
                  "audio/webm", "turn.webm"))
    tr = _json(client.get(f"/api/rooms/{code}"))["turns"][0]["transcript"]
    assert tr["status"] == "failed"
    assert tr["reason"] == "no_speech"


def test_internal_endpoint_requires_oidc(client):
    res = client.post("/api/internal/transcribe", json={"code": "ABC123", "turn": "turn_a1"})
    assert res.status_code == 403
