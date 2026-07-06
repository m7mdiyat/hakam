"""سجال — the optional open-mic closing round.

The invariant that justifies the whole design: سجال is synthesis-only and
NEVER touches the score. A debate judged with a سجال round must produce the
IDENTICAL score/winner/analysis as the same debate without one — the streams
live outside room['turns'], so extraction and scoring are blind to them by
construction. This module pins that, plus the state machine (offer -> accept/
skip -> round -> deliberating), the reconcile time-outs, and the isolated
dual-stream upload feeding the verdict's display-only exchange.
"""
import pytest

from backend import config, state as S
from backend.state import new_room

from .conftest import make_tone
from .test_judge import (_fake_extraction, _fake_probe_or_synth, _full_debate,
                         _json, _submit)
from .test_turn_flow import _room_in_debate


@pytest.fixture(autouse=True)
def _sijal_on():
    prev = config.SIJAL_ENABLED
    config.SIJAL_ENABLED = True
    yield
    config.SIJAL_ENABLED = prev


# --- state machine (pure) ----------------------------------------------------
def _room_after_turns():
    room = new_room("SJTEST", "الموضوع", "tok")
    room["debaters"]["a"].update({"name": "أحمد", "claim": "الأولى"})
    room["debaters"]["b"].update({"name": "سارة", "claim": "الثانية"})
    room["state"] = "turn_b1"
    room["turn_order"] = ["turn_a1", "turn_b1"]
    room["turn_index"] = 1
    return room


def test_last_turn_opens_the_sijal_offer():
    room = _room_after_turns()
    S.advance_turn(room)
    assert room["state"] == S.SIJAL_OFFER
    assert room["sijal"]["a_accepted"] is None
    assert room["sijal"]["offer_deadline_at"] is not None


def test_both_accept_starts_the_round_either_skip_deliberates():
    room = _room_after_turns(); S.advance_turn(room)
    S.sijal_respond(room, "a", True)
    assert room["state"] == S.SIJAL_OFFER          # waiting on B
    S.sijal_respond(room, "b", True)
    assert room["state"] == S.SIJAL
    assert room["sijal"]["deadline_at"] is not None

    room = _room_after_turns(); S.advance_turn(room)
    S.sijal_respond(room, "a", True)
    S.sijal_respond(room, "b", False)              # B skips
    assert room["state"] == S.DELIBERATING


def test_offer_expiry_deliberates():
    from datetime import timedelta
    room = _room_after_turns(); S.advance_turn(room)
    later = room["sijal"]["offer_deadline_at"] + timedelta(seconds=1)
    assert S.reconcile(room, later) is True
    assert room["state"] == S.DELIBERATING


def test_round_deadline_deliberates_even_with_a_missing_stream():
    from datetime import timedelta
    room = _room_after_turns(); S.advance_turn(room)
    S.sijal_respond(room, "a", True); S.sijal_respond(room, "b", True)
    # Only A's stream arrived; B dropped. Past deadline+grace -> judge anyway.
    S.record_sijal_stream(room, "a", "gs://x", 1000, "audio/mp4")
    assert room["state"] == S.SIJAL                # still waiting on B
    late = room["sijal"]["deadline_at"] + timedelta(
        seconds=config.SUBMIT_GRACE_SECONDS + 1)
    assert S.reconcile(room, late) is True
    assert room["state"] == S.DELIBERATING


def test_both_streams_in_deliberates_immediately():
    room = _room_after_turns(); S.advance_turn(room)
    S.sijal_respond(room, "a", True); S.sijal_respond(room, "b", True)
    S.record_sijal_stream(room, "a", "gs://a", 1000, "audio/mp4")
    S.record_sijal_stream(room, "b", "gs://b", 1000, "audio/mp4")
    assert room["state"] == S.DELIBERATING


def test_streams_never_enter_scored_turns():
    room = _room_after_turns(); S.advance_turn(room)
    S.sijal_respond(room, "a", True); S.sijal_respond(room, "b", True)
    S.record_sijal_stream(room, "a", "gs://a", 1000, "audio/mp4")
    # The scored turn list must not grow — streams live under room['sijal'].
    assert all(not t["turn"].startswith("sijal") for t in room["turns"])
    assert room["sijal"]["streams"]["a"]["side"] == "a"


# --- API: offer surfaced, skip path ------------------------------------------
def test_offer_appears_in_view_and_skip_proceeds(client):
    code, token_a, token_b = _room_in_debate(client)
    tone = make_tone(3.0, "webm")
    view = None
    for token in (token_a, token_b, token_a, token_b):
        view = _json(_submit(client, code, token, tone, "audio/webm", "turn.webm"))
    assert view["state"] == "sijal_offer"
    assert view["sijal"]["phase"] == "sijal_offer"

    out = _json(client.post(f"/api/rooms/{code}/sijal/respond",
                            json={"accept": False},
                            headers={"X-Debater-Token": token_a}))
    assert out["state"] == "deliberating"


# --- the fairness invariant: score is identical with vs without سجال ---------
def _judge_verdict(client, monkeypatch, with_sijal):
    monkeypatch.setattr("backend.judge.generate_json", _fake_probe_or_synth)
    monkeypatch.setattr("backend.extraction.generate_json", _fake_extraction)
    fac = pytest.importorskip("backend.factcheck")
    monkeypatch.setattr(fac, "generate_grounded_json",
                        lambda *a, **k: ({"checkable": False, "verdict": "unverifiable",
                                          "explanation_ar": ""}, []))
    code, token_a, token_b = _full_debate_sijal(client, monkeypatch)
    monkeypatch.setattr(config, "GEMINI_ENABLED", True)
    monkeypatch.setattr(config, "FACTCHECK_ENABLED", False)

    if with_sijal:
        import io
        for tk in (token_a, token_b):
            client.post(f"/api/rooms/{code}/sijal/respond", json={"accept": True},
                        headers={"X-Debater-Token": tk})
        tone = make_tone(3.0, "webm")
        for side, tk in (("a", token_a), ("b", token_b)):
            r = client.post(
                f"/api/rooms/{code}/sijal/stream",
                data={"audio": (io.BytesIO(tone), "s.webm", "audio/webm")},
                headers={"X-Debater-Token": tk},
                content_type="multipart/form-data")
            assert r.status_code == 200, r.get_json()
    else:
        client.post(f"/api/rooms/{code}/sijal/respond", json={"accept": False},
                    headers={"X-Debater-Token": token_a})

    view = _json(client.post(f"/api/rooms/{code}/judge",
                             headers={"X-Debater-Token": token_a}))
    assert view["judging_status"] == "done"
    return view["verdict"]


def _full_debate_sijal(client, monkeypatch):
    """Like _full_debate but stops at the سجال offer instead of deliberating."""
    from backend.transcribe import transcribe_turn
    monkeypatch.setattr(config, "TRANSCRIBE_ENABLED", True)
    monkeypatch.setattr("backend.transcribe.generate_json",
                        lambda *a, **k: {"segments": [
                            {"start": "00:00", "end": "00:02",
                             "text": "التعليم عن بعد يوسع الفجوة بين الطلاب"},
                            {"start": "00:02", "end": "00:03",
                             "text": "وأنت شخص فاشل لا يفهم شيئا"}]})
    monkeypatch.setattr("backend.tasks.enqueue_transcription", transcribe_turn)
    code, token_a, token_b = _room_in_debate(client)
    tone = make_tone(3.0, "webm")
    view = None
    for token in (token_a, token_b, token_a, token_b):
        view = _json(_submit(client, code, token, tone, "audio/webm", "turn.webm"))
    assert view["state"] == "sijal_offer"
    return code, token_a, token_b


def test_sijal_does_not_change_the_score(client, monkeypatch):
    without = _judge_verdict(client, monkeypatch, with_sijal=False)
    with_ = _judge_verdict(client, monkeypatch, with_sijal=True)
    # The whole justification for the design: identical scoring, both ways.
    assert with_["score"] == without["score"]
    assert with_["winner"] == without["winner"]
    assert with_["tier"] == without["tier"]
    assert with_["analysis"] == without["analysis"]
    # …but the سجال actually happened and is displayed.
    assert with_["sijal"]["occurred"] is True
    assert without.get("sijal") is None
    assert isinstance(with_["sijal"]["exchange"], list)
