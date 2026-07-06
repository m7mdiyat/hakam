"""فحص الوقائع (Verdict v2.2): claim collection, receipt-floor demotions,
fail-open, the deterministic fact factors, scoring integration (dismissal,
rebuttal voiding, kill-switch), and the full pipeline with a grounded spy.

The invariant that matters most here: a punishing verdict without receipts
must never move a score, and a search failure must never block a verdict.
"""
import pytest

from backend import config, factcheck
from backend.factcheck import claim_key, collect_claims, verify_claims
from backend.judge import _fact_effects, _score_inputs
from backend.scoring import compute_score

from .conftest import make_tone
from .test_judge import (_fake_extraction, _fake_probe_or_synth, _full_debate,
                         _json)


# --- fixtures ----------------------------------------------------------------
def _arg(side, n, premises, cls="inductive", rebuts=None):
    return {
        "id": f"{side}-{n}", "weight": "primary",
        "classification": {"type": cls, "tentative": False},
        "conclusion": {"quote": "خلاصة", "segment_ids": [f"t1-0{n}"]},
        "premises": premises,
        "implicit_premises": [],
        "rebuts": rebuts, "answerable": True, "unanswered": False,
    }


def _prem(external, claim="", quote="اقتباس", segs=("t1-00",)):
    return {"quote": quote, "segment_ids": list(segs),
            "external": external, "external_claim_ar": claim}


def _maps(a_args, b_args):
    return {"a": {"arguments": a_args, "unsupported_assertions": [], "orphan_premises": []},
            "b": {"arguments": b_args, "unsupported_assertions": [], "orphan_premises": []}}


# --- collect_claims ------------------------------------------------------------
def test_collect_dedups_and_accumulates_argument_ids():
    maps = _maps(
        [_arg("a", 1, [_prem(True, "الشمس نجم"), _prem(False)]),
         _arg("a", 2, [_prem(True, "الشمسُ نجمٌ")])],   # same claim, tashkeel
        [_arg("b", 1, [_prem(True, "القمر كوكب")])])
    claims = collect_claims(maps)
    assert len(claims) == 2
    assert claims[0]["argument_ids"] == ["a-1", "a-2"]   # dedup by normalize
    assert claims[1]["side"] == "b"


def test_collect_respects_cap(monkeypatch):
    monkeypatch.setattr(config, "FACTCHECK_CAP", 2)
    args = [_arg("a", i, [_prem(True, f"ادعاء رقم {i}")]) for i in range(1, 5)]
    assert len(collect_claims(_maps(args, []))) == 2


# --- verify: demotions + fail-open ---------------------------------------------
def _spy_grounded(verdict, sources, checkable=True):
    def fake(prompt, schema, **kw):
        return ({"checkable": checkable, "verdict": verdict,
                 "explanation_ar": "شرح"}, sources)
    return fake


SRC2 = [{"title": "who.int", "uri": "https://g/1"},
        {"title": "un.org", "uri": "https://g/2"}]


def _one_claim():
    return [{"key": "k", "claim_ar": "ادعاء", "quote": "اقتباس",
             "segment_ids": ["t1-00"], "side": "a", "argument_ids": ["a-1"]}]


def _verify(monkeypatch, fake):
    monkeypatch.setattr(config, "FACTCHECK_ENABLED", True)
    monkeypatch.setattr(factcheck, "generate_grounded_json", fake)
    return verify_claims("موضوع", _one_claim())["k"]


def test_contradicted_needs_two_source_domains(monkeypatch):
    r = _verify(monkeypatch, _spy_grounded("contradicted", SRC2[:1]))
    assert r["verdict"] == "unverifiable" and r["demoted"] == "receipts"
    # Two chunks from the SAME domain are one receipt, not two.
    same = [dict(SRC2[0]), dict(SRC2[0], uri="https://g/3")]
    r = _verify(monkeypatch, _spy_grounded("contradicted", same))
    assert r["verdict"] == "unverifiable"
    r = _verify(monkeypatch, _spy_grounded("contradicted", SRC2))
    assert r["verdict"] == "contradicted" and len(r["sources"]) == 2


def test_partially_needs_a_source_and_uncheckable_never_rules(monkeypatch):
    assert _verify(monkeypatch, _spy_grounded("partially", []))["verdict"] == "unverifiable"
    assert _verify(monkeypatch, _spy_grounded("partially", SRC2[:1]))["verdict"] == "partially"
    r = _verify(monkeypatch, _spy_grounded("contradicted", SRC2, checkable=False))
    assert r["verdict"] == "unverifiable"     # opinions can't be "refuted"


def test_verifier_failure_fails_open(monkeypatch):
    def boom(*a, **k):
        raise factcheck.GeminiError("search down")
    r = _verify(monkeypatch, boom)
    assert r["verdict"] == "unverifiable" and r["demoted"] == "error"


def test_disabled_yields_unverifiable_without_calls(monkeypatch):
    monkeypatch.setattr(config, "FACTCHECK_ENABLED", False)
    monkeypatch.setattr(factcheck, "generate_grounded_json",
                        lambda *a, **k: pytest.fail("must not call the model"))
    r = verify_claims("موضوع", _one_claim())["k"]
    assert r["verdict"] == "unverifiable" and r["demoted"] == "disabled"


def test_cache_prevents_double_billing(monkeypatch):
    calls = []
    def fake(prompt, schema, **kw):
        calls.append(1)
        return ({"checkable": True, "verdict": "supported",
                 "explanation_ar": ""}, SRC2)
    monkeypatch.setattr(config, "FACTCHECK_ENABLED", True)
    monkeypatch.setattr(factcheck, "generate_grounded_json", fake)
    cache = {}
    verify_claims("موضوع", _one_claim(), cache)
    verify_claims("موضوع", _one_claim(), cache)   # repair round re-verify
    assert len(calls) == 1


# --- fact factors ---------------------------------------------------------------
def _results(**by_claim):
    return {claim_key(k): {"verdict": v, "explanation_ar": "", "sources": []}
            for k, v in by_claim.items()}


def test_factors_deductive_dismissed_inductive_sliver():
    maps = _maps(
        [_arg("a", 1, [_prem(True, "ك1"), _prem(False)], cls="deductive"),
         _arg("a", 2, [_prem(True, "ك1"), _prem(False)], cls="inductive")], [])
    fx = _fact_effects(maps, _results(**{"ك1": "contradicted"}))
    assert fx["a-1"] == {"factor": 0.0, "worst": "contradicted"}
    assert fx["a-2"] == {"factor": 0.3, "worst": "contradicted"}


def test_factors_all_premises_fell_and_partially_and_clean():
    maps = _maps(
        [_arg("a", 1, [_prem(True, "ك1")], cls="inductive"),      # only premise fell
         _arg("a", 2, [_prem(True, "ك2"), _prem(False)]),          # exaggeration
         _arg("a", 3, [_prem(True, "ك3"), _prem(False)])], [])     # unverifiable
    fx = _fact_effects(maps, _results(**{
        "ك1": "contradicted", "ك2": "partially", "ك3": "unverifiable"}))
    assert fx["a-1"]["factor"] == 0.0            # nothing left standing
    assert fx["a-2"] == {"factor": 0.7, "worst": "partially"}
    assert "a-3" not in fx                        # doubt costs nothing


# --- scoring integration ----------------------------------------------------------
EVALS = {"a-1": {"verdict": "strong", "rebuttal_effect": None},
         "b-1": {"verdict": "strong", "rebuttal_effect": "defeated"}}


def test_score_dismissal_and_rebuttal_voiding():
    # B's argument rebuts A's (effect defeated) — but B's own premise was
    # contradicted by the sources: the rebuttal is VOIDED and B's credit dies.
    maps = _maps(
        [_arg("a", 1, [_prem(False)])],
        [_arg("b", 1, [_prem(True, "كذبة")], rebuts={"target_id": "a-1"})])
    facts = {"b-1": {"factor": 0.3, "worst": "contradicted"}}
    a = compute_score(_score_inputs("a", maps, EVALS, [], [], None, facts))
    b = compute_score(_score_inputs("b", maps, EVALS, [], [], None, facts))
    assert a["q"] == 0.9          # survived: the false rebuttal cannot damage
    assert b["q"] == pytest.approx(0.27)   # 0.9 × 0.3
    # And without facts the same rebuttal lands: A drops to 0.9 × 0.3.
    a0 = compute_score(_score_inputs("a", maps, EVALS, [], []))
    assert a0["q"] == pytest.approx(0.27)


def test_ignoring_a_fact_dismissed_point_is_cheap():
    # A never addressed B's answerable argument — but that argument was
    # dismissed by the sources, so the engagement debt shrinks with it.
    maps = _maps(
        [_arg("a", 1, [_prem(False)])],
        [_arg("b", 1, [_prem(True, "كذبة")])])
    facts = {"b-1": {"factor": 0.0, "worst": "contradicted"}}
    u_with = _score_inputs("a", maps, EVALS, [], [], None, facts)["opp_args"][0]["credit"]
    u_without = _score_inputs("a", maps, EVALS, [], [])["opp_args"][0]["credit"]
    assert u_with == 0.0 and u_without == 0.9


# --- full pipeline (grounded spy) --------------------------------------------------
def test_full_pipeline_with_factcheck(client, monkeypatch):
    fact_prompts = []

    def fake_grounded(prompt, schema, **kw):
        fact_prompts.append(prompt)
        return ({"checkable": True, "verdict": "contradicted",
                 "explanation_ar": "المصادر تنقضه"}, SRC2)

    monkeypatch.setattr("backend.judge.generate_json", _fake_probe_or_synth)
    monkeypatch.setattr("backend.extraction.generate_json", _fake_extraction)
    monkeypatch.setattr(factcheck, "generate_grounded_json", fake_grounded)
    code, token_a, _ = _full_debate(client, monkeypatch)
    monkeypatch.setattr(config, "GEMINI_ENABLED", True)
    monkeypatch.setattr(config, "FACTCHECK_ENABLED", True)
    monkeypatch.setattr(config, "FACTCHECK_SCORING", True)

    view = _json(client.post(f"/api/rooms/{code}/judge",
                             headers={"X-Debater-Token": token_a}))
    assert view["judging_status"] == "done"
    v = view["verdict"]

    # A's single argument stood ONLY on the contradicted claim -> dismissed:
    # Q=0, minus the full engagement debt (B's case untouched) -> 0.
    # Without fact-check this exact debate scored 65/24 for A (test_judge).
    assert v["score"]["a"] == 0.0 and v["score"]["b"] == 24.0
    assert v["winner"] == "b"

    # Receipts on the verdict doc.
    fc = v["fact_checks"]
    assert fc["enabled"] and fc["scoring"]
    (claim,) = fc["claims"]
    assert claim["verdict"] == "contradicted" and len(claim["sources"]) == 2
    assert claim["audio"] is not None            # playable like any receipt
    arg_a = v["analysis"]["a"]["arguments"][0]
    assert arg_a["fact_factor"] == 0.0 and arg_a["fact_worst"] == "contradicted"
    assert arg_a["premises"][0]["fact"]["verdict"] == "contradicted"
    assert v["diagnostics"]["facts"]["contradicted"] == 1

    # Anonymization: the verifier never sees the debaters' names.
    assert fact_prompts and all(
        "أحمد" not in p and "سارة" not in p for p in fact_prompts)


def test_full_pipeline_kill_switch_keeps_display_drops_effect(client, monkeypatch):
    monkeypatch.setattr("backend.judge.generate_json", _fake_probe_or_synth)
    monkeypatch.setattr("backend.extraction.generate_json", _fake_extraction)
    monkeypatch.setattr(factcheck, "generate_grounded_json",
                        _spy_grounded("contradicted", SRC2))
    code, token_a, _ = _full_debate(client, monkeypatch)
    monkeypatch.setattr(config, "GEMINI_ENABLED", True)
    monkeypatch.setattr(config, "FACTCHECK_ENABLED", True)
    monkeypatch.setattr(config, "FACTCHECK_SCORING", False)

    view = _json(client.post(f"/api/rooms/{code}/judge",
                             headers={"X-Debater-Token": token_a}))
    v = view["verdict"]
    # Scores match the fact-blind baseline (test_full_v2_pipeline)…
    assert v["score"] == {"a": 65.0, "b": 24.0} and v["winner"] == "a"
    # …but the verification still displays, flagged as non-scoring.
    assert v["fact_checks"]["scoring"] is False
    assert v["fact_checks"]["claims"][0]["verdict"] == "contradicted"
    assert v["analysis"]["a"]["arguments"][0]["fact_factor"] == 0.0
