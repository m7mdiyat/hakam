"""Last-word fairness (Verdict v2.1).

Three mechanisms, all server-validated:
- التحصين المسبق (preemption): the opponent's EARLIER speech already answered
  a final-turn argument nobody could rebut — same survival discipline as a
  rebuttal, receipt required (verbatim quote anchoring in an earlier turn).
- قاعدة الكلمة الأخيرة (untested discount): a final-turn argument nothing
  tested and nothing pre-answered is priced at UNTESTED_FACTOR, never full.
- Both leave answerable arguments completely alone.
"""
import pytest

from backend.judge import (
    AXES,
    _clean_probe,
    _display_map,
    _score_inputs,
    merge_preemptions,
)
from backend.scoring import UNTESTED_FACTOR

# One-round debate: A speaks (t1), B closes (t2). A's second segment is a
# preemptive defense of the very point B later raises.
A_SEGS = ["الرياضة النظامية تحسن الصحة العامة وتقوي القلب",
          "وأما القول بأن الرياضة تضيع وقت الإنسان فمردود لأن تنظيم الوقت مسؤولية صاحبه"]
B_SEGS = ["الرياضة تضيع وقت الإنسان الثمين فلا ينبغي التفرغ لها"]


def _room():
    def turn(key, debater, texts):
        return {
            "turn": key, "debater": debater, "audio_uri": "gs://x",
            "duration_s": 30.0, "forfeited": False,
            "audio_stats": {"silences": []},
            "transcript": {"status": "ok", "segments": [
                {"i": i, "start_s": 10.0 * i, "end_s": 10.0 * (i + 1), "text": t}
                for i, t in enumerate(texts)]},
        }
    return {
        "code": "PREEMPT",
        "debaters": {"a": {"name": "أحمد", "claim": "مفيدة"},
                     "b": {"name": "سالم", "claim": "مضيعة"}},
        "turns": [turn("turn_a1", "a", A_SEGS), turn("turn_b1", "b", B_SEGS)],
    }


def _maps():
    def arg(aid, quote, seg_ids, answerable):
        return {"id": aid, "weight": "primary",
                "classification": {"type": "inductive", "tentative": False},
                "conclusion": {"quote": quote, "segment_ids": seg_ids,
                               "turn": seg_ids[0].split("-")[0]},
                "premises": [], "implicit_premises": [],
                "rebuts": None, "answerable": answerable, "unanswered": False}
    return {
        "a": {"side": "a", "arguments": [arg("a-1", A_SEGS[0], ["t1-00"], True)],
              "unsupported_assertions": [], "orphan_premises": []},
        "b": {"side": "b", "arguments": [arg("b-1", B_SEGS[0], ["t2-00"], False)],
              "unsupported_assertions": [], "orphan_premises": []},
    }


PE_QUOTE = "وأما القول بأن الرياضة تضيع وقت الإنسان فمردود"


def _raw_probe(preemptions):
    return {
        "axes": {l: {ax: {"score": 70} for ax in AXES} for l in ("a", "b")},
        "winner": "a", "argument_evals": [], "preemptions": preemptions,
    }


TRANS = {"أ-1": "a-1", "ب-1": "b-1"}
MAPPING = {"a": "a", "b": "b"}


def _clean(preemptions):
    return _clean_probe(_raw_probe(preemptions), _room(), MAPPING, TRANS, _maps())


# --- probe-level validation --------------------------------------------------
def test_valid_preemption_passes_with_anchored_receipt():
    (pe,) = _clean([{"argument_id": "ب-1", "quote": PE_QUOTE,
                     "segment_ids": ["t1-01"], "effect": "weakened",
                     "explanation_ar": "عالج الاعتراض قبل طرحه"}])["preemptions"]
    assert pe["target_id"] == "b-1" and pe["effect"] == "weakened"
    assert pe["turn"] == "t1" and pe["segment_ids"] == ["t1-01"]


def test_preemption_on_answerable_target_is_dropped():
    # a-1 was answerable (B had a later turn): the normal rebuttal channel
    # owns it — no preemption shortcut.
    out = _clean([{"argument_id": "أ-1", "quote": B_SEGS[0][:30],
                   "segment_ids": ["t2-00"], "effect": "defeated",
                   "explanation_ar": "س"}])
    assert out["preemptions"] == []


def test_preemption_quote_must_precede_the_argument():
    # Defense in depth: even with a WRONG answerable=False flag on a target
    # that has a later opponent turn, a quote anchoring only in that LATER
    # turn (t3) cannot "preempt" an argument raised in t2.
    late = "التنظيم الجيد للوقت يبطل مزاعم التضييع تمامًا وقطعيًا"
    room = _room()
    room["turns"].append({
        "turn": "turn_a2", "debater": "a", "audio_uri": "gs://x",
        "duration_s": 30.0, "forfeited": False, "audio_stats": {"silences": []},
        "transcript": {"status": "ok", "segments": [
            {"i": 0, "start_s": 0.0, "end_s": 10.0, "text": late}]},
    })
    maps = _maps()  # b-1 keeps answerable=False despite A's later turn
    raw = _raw_probe([{"argument_id": "ب-1", "quote": late,
                       "segment_ids": ["t3-00"], "effect": "weakened",
                       "explanation_ar": "س"}])
    out = _clean_probe(raw, room, MAPPING, TRANS, maps)
    assert out["preemptions"] == []


def test_fabricated_preemption_quote_is_dropped():
    out = _clean([{"argument_id": "ب-1", "quote": "جملة لم تقال في المناظرة إطلاقا أبدا",
                   "segment_ids": ["t1-01"], "effect": "weakened",
                   "explanation_ar": "س"}])
    assert out["preemptions"] == []


# --- consensus merge ----------------------------------------------------------
def _pe(effect):
    return {"target_id": "b-1", "turn": "t1", "quote": PE_QUOTE,
            "segment_ids": ["t1-01"], "effect": effect, "explanation_ar": "س"}


def test_merge_needs_three_of_four_and_takes_median_effect():
    probes = [{"preemptions": [_pe("weakened")]},
              {"preemptions": [_pe("defeated")]},
              {"preemptions": [_pe("weakened")]},
              {"preemptions": []}]
    merged = merge_preemptions(probes)
    assert merged["b-1"]["effect"] == "weakened"
    assert merged["b-1"]["found_by"] == 3

    assert merge_preemptions(probes[:1] + [{"preemptions": []}] * 3) == {}


# --- scoring ------------------------------------------------------------------
EVALS = {"a-1": {"verdict": "strong", "rebuttal_effect": None,
                 "failure_point_ar": "", "classification_agree": True,
                 "alt_classification": None},
         "b-1": {"verdict": "strong", "rebuttal_effect": None,
                 "failure_point_ar": "", "classification_agree": True,
                 "alt_classification": None}}


def test_preempted_argument_suffers_the_effect():
    inp = _score_inputs("b", _maps(), EVALS, [], [],
                        {"b-1": {"effect": "weakened"}})
    assert inp["credits"] == [pytest.approx(0.9 * 0.7)]


def test_untested_last_word_argument_is_discounted():
    inp = _score_inputs("b", _maps(), EVALS, [], [], {})
    assert inp["credits"] == [pytest.approx(0.9 * UNTESTED_FACTOR)]


def test_answerable_argument_keeps_full_credit():
    inp = _score_inputs("a", _maps(), EVALS, [], [], {})
    assert inp["credits"] == [pytest.approx(0.9)]


# --- display ------------------------------------------------------------------
ARG_RESULTS = {aid: {"classification": "inductive", "tentative": False,
                     "verdict": "strong", "failure_point_ar": "",
                     "rebuttal_effect": None, "contested": False}
               for aid in ("a-1", "b-1")}


def test_display_carries_preempted_receipt_and_untested_flag():
    shown = _display_map(_room(), _maps(), ARG_RESULTS,
                         {"b-1": {"quote": PE_QUOTE, "segment_ids": ["t1-01"],
                                  "effect": "weakened", "explanation_ar": "س"}})
    (b_arg,) = shown["b"]["arguments"]
    assert b_arg["preempted"]["effect"] == "weakened"
    assert b_arg["preempted"]["audio"]["turn"] == "turn_a1"  # opponent's voice
    assert b_arg["untested"] is False  # preempted = it WAS tested

    shown = _display_map(_room(), _maps(), ARG_RESULTS, {})
    (b_arg,) = shown["b"]["arguments"]
    assert b_arg["preempted"] is None
    assert b_arg["untested"] is True
    (a_arg,) = shown["a"]["arguments"]
    assert a_arg["untested"] is False
