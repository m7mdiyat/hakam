"""درجة الحجاج — the deterministic Verdict-v2 score (user-approved model).

Philosophy: QUALITY dominates, engagement is a duty.
- Construction quality Q is best-prefix-weighted: a single airtight argument
  hits the ceiling (Q=1.0) and extra arguments can only help or do nothing
  (max-over-k means a weak addition silently drops out — no dilution, no
  "say less" incentive).
- Engagement U is the quality-weighted fraction of the opponent's ANSWERABLE
  case left unaddressed. Attempting a rebuttal counts even if it fails
  (engaging-and-losing is free; refusing to engage is not) — EXCEPT a
  rebuttal carrying a consensus straw-man card, which addressed a distortion,
  not the argument.
- score = 100·Q − ENGAGE_MAX·U − deductions, clamped to [0, 100].

External-claim truth affects nothing here (locked design decision): the judge
cannot score on what it declined to verify. Lack of SUPPORT scores via the
soundness deductions.

All constants are Gate-3 priors. LINKED_FALLACY_FACTOR = 1.0 is a SETTLED
calibration decision (user-approved): a fallacy linked to an argument that
already scored negatively bills both penalties — the weak-argument verdict
and the fallacy deduction are two distinct offenses.
"""
from __future__ import annotations

CREDIT = {"valid": 1.0, "strong": 0.9, "contested": 0.5, "invalid": 0.35, "weak": 0.35}
ASSERTION_FLOOR = 0.15          # a debater with only bare assertions
RANK_W = [1.0, 0.5, 0.25, 0.125]
SURVIVAL = {"defeated": 0.3, "weakened": 0.7, "unaffected": 1.0}
ENGAGE_MAX = 25.0
DEDUCT_FALLACY = {"high": 8.0, "medium": 5.0, "low": 2.0}
DEDUCT_SOUNDNESS = {"self_contradiction": 10.0, "claim_abandonment": 8.0,
                    "unsupported_load_bearing": 6.0, "premise_conclusion_drift": 5.0}
LINKED_FALLACY_FACTOR = 1.0


def construction_quality(surviving_credits: list) -> float:
    """Best-prefix weighted mean over credits sorted descending."""
    if not surviving_credits:
        return 0.0
    cs = sorted(surviving_credits, reverse=True)[: len(RANK_W)]
    best, num, den = 0.0, 0.0, 0.0
    for w, c in zip(RANK_W, cs):
        num += w * c
        den += w
        best = max(best, num / den)
    return best


def surviving_credit(verdict: str, worst_suffered_effect: str) -> float:
    return CREDIT.get(verdict, 0.0) * SURVIVAL.get(worst_suffered_effect, 1.0)


def engagement_u(opp_args: list) -> float:
    """opp_args: [{'credit': float, 'answerable': bool, 'addressed': bool}].
    Quality-weighted (rank-decayed) fraction of the answerable case ignored."""
    answerable = sorted((a for a in opp_args if a["answerable"]),
                        key=lambda a: a["credit"], reverse=True)
    if not answerable:
        return 0.0
    num = den = 0.0
    for i, a in enumerate(answerable):
        w = RANK_W[i] if i < len(RANK_W) else RANK_W[-1]
        den += w * a["credit"]
        if not a["addressed"]:
            num += w * a["credit"]
    return (num / den) if den > 0 else 0.0


def deductions(fallacies: list, soundness: list, negative_arg_ids: set) -> float:
    """fallacies: [{'severity', 'argument_id'|None}], soundness: [{'type'}].
    negative_arg_ids: arguments already judged invalid/weak (the double-count
    question — LINKED_FALLACY_FACTOR applies to fallacies linked to them)."""
    total = 0.0
    for f in fallacies:
        d = DEDUCT_FALLACY.get(f.get("severity"), 0.0)
        if f.get("argument_id") in negative_arg_ids:
            d *= LINKED_FALLACY_FACTOR
        total += d
    for s in soundness:
        total += DEDUCT_SOUNDNESS.get(s.get("type"), 0.0)
    return total


def compute_score(own: dict) -> dict:
    """own: {'credits': [surviving credits], 'has_assertions': bool,
             'opp_args': [engagement_u input], 'fallacies': [...],
             'soundness': [...], 'negative_arg_ids': set}
    Returns {'score', 'q', 'u', 'deductions'} — the displayable breakdown."""
    q = construction_quality(own["credits"])
    if q == 0.0 and own.get("has_assertions"):
        q = ASSERTION_FLOOR
    u = engagement_u(own.get("opp_args", []))
    ded = deductions(own.get("fallacies", []), own.get("soundness", []),
                     own.get("negative_arg_ids", set()))
    score = max(0.0, min(100.0, 100.0 * q - ENGAGE_MAX * u - ded))
    return {"score": round(score, 1), "q": round(q, 3), "u": round(u, 3),
            "deductions": round(ded, 1)}
