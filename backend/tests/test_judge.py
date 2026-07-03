"""Verdict-v2 judge: arabic anchoring utilities, merge/tier units, and the full
mocked pipeline (extraction fake + 4 mapping-aware probe fakes + synthesis)."""
import pytest

from backend import config
from backend.arabic import find_span, normalize, strip_names
from backend.judge import decide_tier, merge_arg_evals, merge_axes, merge_findings
from backend.schemas import AXES

from .conftest import make_tone
from .test_turn_flow import _json, _room_in_debate, _submit


# --- arabic utilities (unchanged behavior) -----------------------------------
def test_normalize_folds_variants():
    assert normalize("الحُجَّةُ") == normalize("الحجه")
    assert normalize("إنترنت") == normalize("انترنت")


SEGS = [
    {"i": 0, "start_s": 0.0, "end_s": 5.0, "text": "أرى أن التعليم عن بعد يوسع الفجوة"},
    {"i": 1, "start_s": 5.0, "end_s": 9.0, "text": "والسبب تفاوت جودة الإنترنت بين البيوت"},
]


def test_find_span_exact_and_fuzzy():
    assert find_span("تفاوت جودة الإنترنت", SEGS) == (1, 1)
    assert find_span("والسبب تفاوت جودة النت بين البيوت", SEGS) == (1, 1)
    assert find_span("عبارة لم تقال في المناظرة إطلاقا أبدا", SEGS) is None


def test_strip_names_exact_tokens_only():
    assert strip_names("قال أحمد إن أحمدك لن يفهم", ["أحمد"]) == \
        "قال المتناظر إن أحمدك لن يفهم"


# --- merge units ---------------------------------------------------------------
def _map(side, n_args=1, rebuts=None, answerable=True):
    args = []
    for i in range(1, n_args + 1):
        args.append({
            "id": f"{side}-{i}", "weight": "primary" if i == 1 else "secondary",
            "conclusion": {"quote": "ق", "segment_ids": [f"t1-0{i}"], "turn": "t1"},
            "premises": [], "implicit_premises": [],
            "classification": {"type": "inductive", "tentative": False, "rationale_ar": ""},
            "rebuts_segments": [], "rebuts": rebuts if i == 1 else None,
            "answerable": answerable, "unanswered": False,
        })
    return {"side": side, "arguments": args,
            "unsupported_assertions": [], "orphan_premises": []}


def _probe_eval(verdict="strong", agree=True, alt="inductive", effect="not_applicable"):
    return {"verdict": verdict, "failure_point_ar": "خلل" if verdict in ("weak", "invalid") else "",
            "classification_agree": agree, "alt_classification": alt,
            "rebuttal_effect": effect}


def _probe(evals, soundness=(), fallacies=(), issues=(), holistic="a"):
    return {"axes": {s: {ax: 70 for ax in AXES} for s in ("a", "b")},
            "evals": evals, "soundness": list(soundness), "fallacies": list(fallacies),
            "issues": list(issues), "holistic": holistic, "confidence": "high"}


def test_verdict_consensus_three_of_four():
    maps = {"a": _map("a"), "b": _map("b", 0)}
    probes = [_probe({"a-1": _probe_eval("strong")})] * 3 + \
             [_probe({"a-1": _probe_eval("weak")})]
    r = merge_arg_evals(probes, maps)["a-1"]
    assert r["verdict"] == "strong" and not r["contested"]


def test_verdict_two_two_is_contested():
    maps = {"a": _map("a"), "b": _map("b", 0)}
    probes = [_probe({"a-1": _probe_eval("strong")})] * 2 + \
             [_probe({"a-1": _probe_eval("weak")})] * 2
    assert merge_arg_evals(probes, maps)["a-1"]["contested"] is True


def test_classification_override_needs_three_and_flips_family():
    maps = {"a": _map("a"), "b": _map("b", 0)}
    probes = [_probe({"a-1": _probe_eval("valid", agree=False, alt="deductive")})] * 3 \
        + [_probe({"a-1": _probe_eval("strong")})]
    r = merge_arg_evals(probes, maps)["a-1"]
    assert r["classification"] == "deductive" and r["tentative"] is True
    assert r["verdict"] == "valid"


def test_rebuttal_effect_is_ordinal_median():
    maps = {"a": _map("a", rebuts={"target_id": "b-1"}), "b": _map("b")}
    effects = ["defeated", "weakened", "weakened", "unaffected"]
    probes = [_probe({"a-1": _probe_eval(effect=e), "b-1": _probe_eval()}) for e in effects]
    assert merge_arg_evals(probes, maps)["a-1"]["rebuttal_effect"] == "weakened"


def test_findings_need_three_of_four():
    fal = {"speaker": "b", "type": "ad_hominem", "argument_id": "b-1",
           "segment_ids": ["t2-01"], "quote": "ق", "turn": "t2",
           "explanation_ar": "", "severity": "high"}
    three = [_probe({}, fallacies=[dict(fal)])] * 3 + [_probe({})]
    two = [_probe({}, fallacies=[dict(fal)])] * 2 + [_probe({})] * 2
    assert len(merge_findings(three)[0]) == 1
    assert len(merge_findings(two)[0]) == 0


# --- tier decision --------------------------------------------------------------
def test_tier_close_when_majority_disagrees_with_score():
    tier, w = decide_tier(votes=["a", "a", "a", "b"], margin=20, score_winner="b",
                          axes_lean="b", spread=0, contested=0, incoherent=0,
                          audits=0, repaired=False, valid_probes=4)
    assert (tier, w) == ("close", None)


def test_tier_close_on_axes_structure_conflict_at_small_margin():
    tier, _ = decide_tier(votes=["a"] * 4, margin=5, score_winner="a",
                          axes_lean="b", spread=0, contested=0, incoherent=0,
                          audits=0, repaired=False, valid_probes=4)
    assert tier == "close"


def test_tier_margin_tolerates_single_dissent():
    # The prod-smoke case: 18.7-point gap, one probe tied (abstained), one
    # dissented -> no strict majority, but the margin tolerance holds the win.
    tier, w = decide_tier(votes=["a", "a", "b"], margin=18.7, score_winner="a",
                          axes_lean="a", spread=0, contested=0, incoherent=0,
                          audits=0, repaired=False, valid_probes=4)
    assert (tier, w) == ("medium", "a")
    # Below the tolerance threshold the strict rule still applies.
    tier, w = decide_tier(votes=["a", "a", "b"], margin=10, score_winner="a",
                          axes_lean="a", spread=0, contested=0, incoherent=0,
                          audits=0, repaired=False, valid_probes=4)
    assert (tier, w) == ("close", None)
    # Two dissents are never tolerated, whatever the margin.
    tier, w = decide_tier(votes=["a", "b", "b"], margin=30, score_winner="a",
                          axes_lean="a", spread=0, contested=0, incoherent=0,
                          audits=0, repaired=False, valid_probes=4)
    assert (tier, w) == ("close", None)


def test_ad_hominem_severity_is_tone_based_and_capped():
    from backend.judge import _severity_final
    maps = {"a": _map("a"), "b": _map("b")}
    harsh = {"type": "ad_hominem", "severity": "high", "argument_id": "b-1"}
    mild = {"type": "ad_hominem", "severity": "low", "argument_id": "b-1"}
    other = {"type": "straw_man", "severity": "low", "argument_id": "b-1"}
    assert _severity_final(harsh, maps) == "medium"   # capped, never high
    assert _severity_final(mild, maps) == "low"       # doubt goes lower
    assert _severity_final(other, maps) == "high"     # others: linkage (primary)


def test_tier_high_requires_clean_sweep():
    args = dict(votes=["a"] * 4, margin=20, score_winner="a", axes_lean="a",
                spread=0, contested=0, incoherent=0, audits=0, repaired=False,
                valid_probes=4)
    assert decide_tier(**args) == ("high", "a")
    assert decide_tier(**{**args, "repaired": True})[0] == "medium"
    assert decide_tier(**{**args, "contested": 1})[0] == "medium"


# --- full mocked pipeline --------------------------------------------------------
TRANSCRIPT = {"segments": [
    {"start": "00:00", "end": "00:02", "text": "التعليم عن بعد يوسع الفجوة بين الطلاب"},
    {"start": "00:02", "end": "00:03", "text": "وأنت شخص فاشل لا يفهم شيئا"},
]}


def _full_debate(client, monkeypatch):
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


def _fake_extraction(prompt, schema, **kw):
    """Target is «أ» in its own call; sniff which real debater via the claim."""
    target_a = "«أ»: الدعوى الأولى" in prompt
    concl_tid, prem_tid = ("t1", "t3") if target_a else ("t2", "t4")
    arg = {
        "rebuts_segments": [] if target_a else ["t1-00"],   # B rebuts A's argument
        "conclusion": {"segment_ids": [f"{concl_tid}-00"],
                       "quote": "التعليم عن بعد يوسع الفجوة بين الطلاب"},
        "premises": [{"segment_ids": [f"{prem_tid}-00"],
                      "quote": "التعليم عن بعد يوسع الفجوة بين الطلاب",
                      "external": target_a,
                      "external_claim_ar": "إحصاءات الفجوة التعليمية" if target_a else ""}],
        "implicit_premises": [{"why_needed_ar": "س", "text_ar": "مقدمة مضمرة للاختبار"}],
        "classification": {"rationale_ar": "س", "type": "inductive", "tentative": False},
        "weight": "primary",
    }
    return {"arguments": [arg], "unsupported_assertions": [], "orphan_premises": []}


def _fake_probe_or_synth(prompt, schema, **kw):
    if "النتائج النهائية المحسومة" in prompt:  # synthesis
        prof = {"strongest_ar": "قوي", "weakest_ar": "ضعيف", "tip_ar": "نصيحة"}
        return {"key_moment": {"turn": "t2", "segment_ids": ["t2-01"],
                               "description_ar": "انفعال المتحدث «ب»"},
                "profiles": {"a": prof, "b": prof},
                "reasoning_ar": "حسم المتحدث «أ» المناظرة بوضوح."}
    a_label = "a" if "«أ»: الدعوى الأولى" in prompt else "b"
    b_label = "b" if a_label == "a" else "a"
    a_id, b_id = f"{'أ' if a_label == 'a' else 'ب'}-1", f"{'ب' if a_label == 'a' else 'أ'}-1"
    hi = {ax: {"analysis": "ت", "score": 80} for ax in AXES}
    lo = {ax: {"analysis": "ت", "score": 60} for ax in AXES}
    return {
        "argument_evals": [
            {"argument_id": a_id, "analysis_ar": "ت", "verdict": "strong",
             "failure_point_ar": "", "classification_agree": True,
             "alt_classification": "inductive", "rebuttal_effect": "not_applicable"},
            {"argument_id": b_id, "analysis_ar": "ت", "verdict": "weak",
             "failure_point_ar": "عينة ضيقة", "classification_agree": True,
             "alt_classification": "inductive", "rebuttal_effect": "unaffected"},
        ],
        "soundness": [{"speaker": b_label, "argument_id": b_id,
                       "quotes": [{"segment_ids": ["t4-00"],
                                   "quote": "التعليم عن بعد يوسع الفجوة بين الطلاب"}],
                       "explanation_ar": "سيق بلا دعم",
                       "type": "unsupported_load_bearing"}],
        "fallacies": [{"speaker": b_label, "turn": "t2", "segment_ids": ["t2-01"],
                       "quote": "وأنت شخص فاشل لا يفهم شيئا",
                       "explanation_ar": "هجوم على الشخص", "fallacy_type": "ad_hominem",
                       "severity": "high", "argument_id": b_id}],
        "extraction_issues": [],
        "axes": {a_label: hi, b_label: lo},
        "winner": a_label, "confidence": "high",
    }


def test_full_v2_pipeline(client, monkeypatch):
    prompts = []

    def spy_probe(prompt, schema, **kw):
        prompts.append(prompt)
        return _fake_probe_or_synth(prompt, schema, **kw)

    def spy_extract(prompt, schema, **kw):
        prompts.append(prompt)
        return _fake_extraction(prompt, schema, **kw)

    monkeypatch.setattr("backend.judge.generate_json", spy_probe)
    monkeypatch.setattr("backend.extraction.generate_json", spy_extract)
    code, token_a, _ = _full_debate(client, monkeypatch)
    monkeypatch.setattr(config, "GEMINI_ENABLED", True)

    view = _json(client.post(f"/api/rooms/{code}/judge",
                             headers={"X-Debater-Token": token_a}))
    assert view["judging_status"] == "done"
    v = view["verdict"]
    assert v["schema_version"] == 2

    # Scoring: A = 100·0.9 − 25·U(=1: ignored B's answerable arg) = 65.
    #          B = 100·0.35 − 0 − (5 ad-hominem [tone-capped medium] + 6 soundness) = 24.
    assert v["score"] == {"a": 65.0, "b": 24.0}
    assert (v["tier"], v["winner"]) == ("high", "a")
    assert v["margin"]["value"] == 41.0 and v["margin"]["band"] == "decisive"

    # Section 1: anchors on quoted material, none on the implicit premise.
    arg_a = v["analysis"]["a"]["arguments"][0]
    assert arg_a["conclusion"]["audio"]["turn"] == "turn_a1"
    assert arg_a["premises"][0]["audio"] is not None
    assert "audio" not in arg_a["implicit_premises"][0]
    assert arg_a["unanswered"] is False          # B rebutted it
    arg_b = v["analysis"]["b"]["arguments"][0]
    assert arg_b["rebuts"] == {"target_id": "a-1", "effect": "unaffected"}
    assert arg_b["unanswered"] is True           # A never engaged it

    # Section 2: linked fallacy (rule-derived severity), soundness, registry.
    (card,) = v["fallacies"]
    assert card["argument_id"] == "b-1" and card["severity"] == "medium"  # tone-capped
    assert card["audio"] is not None
    (snd,) = v["soundness"]
    assert snd["type"] == "unsupported_load_bearing" and snd["quotes"][0]["audio"]
    (ext,) = v["external_claims"]
    assert ext["speaker"] == "a" and ext["argument_id"] == "a-1"

    # Axes strip retained; anonymization boundary; de-anonymized narrative.
    assert v["scores"]["a"]["logic"] == 80
    assert all("أحمد" not in p and "سارة" not in p for p in prompts)
    assert v["reasoning_ar"] == "حسم أحمد المناظرة بوضوح."

    # Idempotent retrigger.
    again = _json(client.post(f"/api/rooms/{code}/judge",
                              headers={"X-Debater-Token": token_a}))
    assert again["verdict"]["diagnostics"] == v["diagnostics"]


def test_label_bias_probe_flips_to_close(client, monkeypatch):
    """A judge that always favors label «أ» must produce a draw."""
    def biased(prompt, schema, **kw):
        out = _fake_probe_or_synth(prompt, schema, **kw)
        if "النتائج النهائية المحسومة" in prompt:
            return out
        # Whatever the mapping, «أ»'s argument wins and «ب»'s loses.
        for ev in out["argument_evals"]:
            ev["verdict"] = "strong" if ev["argument_id"].startswith("أ") else "weak"
        out["winner"] = "a"
        out["fallacies"], out["soundness"] = [], []
        hi = {ax: {"analysis": "ت", "score": 80} for ax in AXES}
        lo = {ax: {"analysis": "ت", "score": 60} for ax in AXES}
        out["axes"] = {"a": hi, "b": lo}
        return out

    monkeypatch.setattr("backend.judge.generate_json", biased)
    monkeypatch.setattr("backend.extraction.generate_json", _fake_extraction)
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
    monkeypatch.setattr("backend.extraction.generate_json", boom)
    code, token_a, _ = _full_debate(client, monkeypatch)
    monkeypatch.setattr(config, "GEMINI_ENABLED", True)
    view = _json(client.post(f"/api/rooms/{code}/judge",
                             headers={"X-Debater-Token": token_a}))
    assert view["judging_status"] == "failed" and view["verdict"] is None

    monkeypatch.setattr("backend.judge.generate_json", _fake_probe_or_synth)
    monkeypatch.setattr("backend.extraction.generate_json", _fake_extraction)
    view = _json(client.post(f"/api/rooms/{code}/judge",
                             headers={"X-Debater-Token": token_a}))
    assert view["judging_status"] == "done" and view["verdict"]["winner"] == "a"
