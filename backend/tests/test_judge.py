"""Judge ensemble tests: pure merge logic, Arabic anchoring, the mechanical
answerability rule, and the full flow with the model mocked."""
import io

import pytest

from backend import config
from backend.arabic import find_span, normalize, strip_names
from backend.judge import merge_probes
from backend.schemas import AXES

from .conftest import make_tone
from .test_turn_flow import _json, _room_in_debate, _submit


# --- arabic utilities --------------------------------------------------------
def test_normalize_folds_variants():
    assert normalize("الحُجَّةُ") == normalize("الحجه")
    assert normalize("إنترنت") == normalize("انترنت")
    assert normalize("رأيي، واضحٌ!") == "راي" + "ي واضح"


SEGS = [
    {"i": 0, "start_s": 0.0, "end_s": 5.0, "text": "أرى أن التعليم عن بعد يوسع الفجوة"},
    {"i": 1, "start_s": 5.0, "end_s": 9.0, "text": "والسبب تفاوت جودة الإنترنت بين البيوت"},
    {"i": 2, "start_s": 9.0, "end_s": 14.0, "text": "ولهذا أطالب بالعودة الحضورية"},
]


def test_find_span_exact_within_one_segment():
    assert find_span("تفاوت جودة الإنترنت", SEGS) == (1, 1)


def test_find_span_across_segments():
    assert find_span("يوسع الفجوة والسبب تفاوت", SEGS) == (0, 1)


def test_find_span_fuzzy_tolerates_a_particle():
    # Judge quoted with one word off ("جوده النت" vs "جودة الإنترنت").
    assert find_span("والسبب تفاوت جودة النت بين البيوت", SEGS) == (1, 1)


def test_find_span_rejects_fabricated_quote():
    assert find_span("العبارة هذه لم تقال في المناظرة إطلاقا أبدا", SEGS) is None


def test_strip_names_exact_tokens_only():
    out = strip_names("قال أحمد إن أحمدك لن يفهم يا سارة", ["أحمد", "سارة"])
    assert out == "قال المتناظر إن أحمدك لن يفهم يا المتناظر"


# --- merge_probes ------------------------------------------------------------
def _probe(a=80, b=60, computed=None, holistic=None, fallacies=(), dropped=()):
    axes = {"a": {ax: a for ax in AXES}, "b": {ax: b for ax in AXES}}
    comp = computed or ("a" if a > b else "b" if b > a else None)
    return {"axes": axes, "fallacies": list(fallacies), "dropped": list(dropped),
            "holistic": holistic or comp or "a", "computed": comp,
            "confidence": "high"}


FAL = {"speaker": "b", "turn": "t2", "segment_ids": ["t2-00"],
       "quote": "اقتباس", "explanation_ar": "شرح", "type": "ad_hominem",
       "severity": "high"}


def test_merge_unanimous_big_margin_is_high_tier():
    m = merge_probes([_probe() for _ in range(4)])
    assert (m["tier"], m["winner"], m["margin_band"]) == ("high", "a", "decisive")
    assert m["scores"]["a"]["logic"] == 80 and m["scores"]["b"]["clarity"] == 60


def test_merge_three_one_votes_is_medium():
    probes = [_probe(), _probe(), _probe(), _probe(a=58, b=62)]
    m = merge_probes(probes)
    assert m["tier"] == "medium" and m["winner"] == "a"


def test_merge_label_flip_two_two_is_close():
    m = merge_probes([_probe(), _probe(), _probe(a=60, b=80), _probe(a=60, b=80)])
    assert m["tier"] == "close" and m["winner"] is None


def test_merge_tiny_margin_forced_close_even_if_unanimous():
    m = merge_probes([_probe(a=71, b=69) for _ in range(4)])
    assert m["tier"] == "close" and m["winner"] is None and m["margin_band"] is None


def test_merge_huge_spread_is_close():
    m = merge_probes([_probe(a=95, b=60), _probe(a=65, b=60),
                      _probe(a=95, b=60), _probe(a=66, b=60)])
    assert m["diagnostics"]["axis_spread_max"] == 30
    assert m["tier"] == "close"


def test_merge_incoherent_probe_downgrades_high_to_medium():
    probes = [_probe() for _ in range(3)] + [_probe(holistic="b")]
    m = merge_probes(probes)
    assert m["tier"] == "medium" and m["diagnostics"]["incoherent_probes"] == 1


def test_fallacy_consensus_needs_three_of_four():
    three = [_probe(fallacies=[dict(FAL)]) for _ in range(3)] + [_probe()]
    two = [_probe(fallacies=[dict(FAL)]) for _ in range(2)] + [_probe(), _probe()]
    assert len(merge_probes(three)["fallacies"]) == 1
    assert len(merge_probes(two)["fallacies"]) == 0


def test_inapplicable_axis_excluded_from_scores_spread_and_totals():
    # Probes wildly disagree on A's rebuttal (they can only guess when A never
    # had a rebuttal opportunity) — exclusion must keep the tier stable.
    probes = []
    for noise in (95, 40, 90, 45):
        p = _probe()
        p["axes"]["a"]["rebuttal"] = noise
        probes.append(p)
    m = merge_probes(probes, inapplicable={("a", "rebuttal")})
    assert m["scores"]["a"]["rebuttal"] is None
    assert m["diagnostics"]["axis_spread_max"] == 0
    assert m["tier"] == "high" and m["winner"] == "a"
    assert m["margin"] == 20.0  # mean over the 4 applicable axes only


def test_fallacy_severity_is_modal_with_milder_tiebreak():
    probes = [
        _probe(fallacies=[{**FAL, "severity": "high"}]),
        _probe(fallacies=[{**FAL, "severity": "low"}]),
        _probe(fallacies=[{**FAL, "severity": "high"}]),
        _probe(fallacies=[{**FAL, "severity": "low"}]),
    ]
    assert merge_probes(probes)["fallacies"][0]["severity"] == "low"


# --- end-to-end with mocked model ---------------------------------------------
TRANSCRIPT = {"segments": [
    {"start": "00:00", "end": "00:02", "text": "التعليم عن بعد يوسع الفجوة بين الطلاب"},
    {"start": "00:02", "end": "00:03", "text": "وأنت شخص فاشل لا يفهم شيئا"},
]}


def _full_debate(client, monkeypatch):
    """Room through all 4 turns with transcription mocked inline."""
    from backend.transcribe import transcribe_turn

    monkeypatch.setattr(config, "TRANSCRIBE_ENABLED", True)
    monkeypatch.setattr("backend.transcribe.generate_json", lambda *a, **k: TRANSCRIPT)
    monkeypatch.setattr("backend.tasks.enqueue_transcription", transcribe_turn)

    code, token_a, token_b = _room_in_debate(client)
    tone = make_tone(3.0, "webm")
    for token in (token_a, token_b, token_a, token_b):
        view = _json(_submit(client, code, token, tone, "audio/webm", "turn.webm"))
    assert view["state"] == "deliberating"
    return code, token_a, token_b


def _fake_judge_generate(prompt, schema, **kw):
    """Simulates a CONSISTENT judge: favors the real debater A whatever the
    label mapping, and flags one ad hominem on real debater B (turn t2)."""
    if "النتائج النهائية المحسومة" in prompt:  # synthesis call
        prof = {"strongest_ar": "قوي", "weakest_ar": "ضعيف", "tip_ar": "نصيحة"}
        return {"key_moment": {"turn": "t2", "segment_ids": ["t2-01"],
                               "description_ar": "لحظة الانفعال"},
                "profiles": {"a": prof, "b": prof},
                "reasoning_ar": "حجج الطرف الفائز كانت أرصن."}
    # Which label is real A? The claims block pins real A's claim to a label.
    a_label = "a" if "«أ»: الدعوى الأولى" in prompt else "b"
    b_label = "b" if a_label == "a" else "a"
    hi = {ax: {"analysis": "تحليل", "score": 80} for ax in AXES}
    lo = {ax: {"analysis": "تحليل", "score": 60} for ax in AXES}
    return {
        "axes": {a_label: hi, b_label: lo},
        "fallacies": [{
            "speaker": b_label, "turn": "t2", "segment_ids": ["t2-01"],
            "quote": "وأنت شخص فاشل لا يفهم شيئا",
            "explanation_ar": "هجوم على الشخص بدل الحجة",
            "fallacy_type": "ad_hominem", "severity": "high",
        }],
        "dropped_points": [{
            "raised_turn": "t1", "segment_ids": ["t1-00"],
            "point_ar": "اتساع الفجوة بين الطلاب",
            "speaker": b_label,
        }],
        "winner": a_label, "confidence": "high",
    }


def test_full_judging_flow(client, monkeypatch):
    monkeypatch.setattr("backend.judge.generate_json", _fake_judge_generate)
    code, token_a, _ = _full_debate(client, monkeypatch)
    monkeypatch.setattr(config, "GEMINI_ENABLED", True)

    view = _json(client.post(f"/api/rooms/{code}/judge",
                             headers={"X-Debater-Token": token_a}))
    assert view["judging_status"] == "done"
    v = view["verdict"]
    assert v["tier"] == "high" and v["winner"] == "a"
    assert v["margin"] == {"value": 20.0, "band": "decisive"}
    assert v["scores"]["a"]["logic"] == 80 and v["scores"]["b"]["composure"] == 60

    (card,) = v["fallacies"]
    assert card["speaker"] == "b" and card["name_ar"] == "الشخصنة"
    assert card["turn"] == "turn_b1"
    # Anchor: segment 1 of t2 runs 2.0-3.0s; padded early-biased window.
    assert card["audio"]["start_s"] == 0.5 and card["audio"]["end_s"] == 3.0

    (dp,) = v["dropped_points"]
    assert dp["speaker"] == "b" and dp["raised_turn"] == "turn_a1"

    # Emotionality derives from composure + the emotional-register fallacy.
    assert v["emotionality"] == {"a": 20, "b": 45}
    assert v["key_moment"]["turn"] == "turn_b1"
    assert v["diagnostics"]["probes_valid"] == 4

    # Idempotent: re-triggering does not re-judge a done room.
    again = _json(client.post(f"/api/rooms/{code}/judge",
                              headers={"X-Debater-Token": token_a}))
    assert again["verdict"]["diagnostics"] == v["diagnostics"]


def test_label_bias_probe_flips_to_close(client, monkeypatch):
    """A judge that always favors label «أ» (pure position bias) must produce a
    draw, not a winner — this is the whole point of the 2x2 ensemble."""
    def biased(prompt, schema, **kw):
        out = _fake_judge_generate(prompt, schema, **kw)
        if "النتائج النهائية المحسومة" in prompt:
            return out
        hi = {ax: {"analysis": "ت", "score": 80} for ax in AXES}
        lo = {ax: {"analysis": "ت", "score": 60} for ax in AXES}
        out["axes"] = {"a": hi, "b": lo}   # label «أ» always wins
        out["winner"] = "a"
        return out

    monkeypatch.setattr("backend.judge.generate_json", biased)
    code, token_a, _ = _full_debate(client, monkeypatch)
    monkeypatch.setattr(config, "GEMINI_ENABLED", True)

    v = _json(client.post(f"/api/rooms/{code}/judge",
                          headers={"X-Debater-Token": token_a}))["verdict"]
    assert v["tier"] == "close" and v["winner"] is None


def test_failed_judging_is_retriggerable(client, monkeypatch):
    from backend.gemini import GeminiError

    def boom(*a, **kw):
        raise GeminiError("model down")

    monkeypatch.setattr("backend.judge.generate_json", boom)
    code, token_a, _ = _full_debate(client, monkeypatch)
    monkeypatch.setattr(config, "GEMINI_ENABLED", True)

    view = _json(client.post(f"/api/rooms/{code}/judge",
                             headers={"X-Debater-Token": token_a}))
    assert view["judging_status"] == "failed" and view["verdict"] is None

    monkeypatch.setattr("backend.judge.generate_json", _fake_judge_generate)
    view = _json(client.post(f"/api/rooms/{code}/judge",
                             headers={"X-Debater-Token": token_a}))
    assert view["judging_status"] == "done" and view["verdict"]["winner"] == "a"
