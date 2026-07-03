"""درجة الحجاج — pins the user-approved quality-dominant model, including the
five review scenarios (dodging airtight = 75 vs engaging airtight = 100)."""
import pytest

from backend.scoring import (ASSERTION_FLOOR, LINKED_FALLACY_FACTOR,
                             compute_score, construction_quality, deductions,
                             engagement_u)


def _own(credits, opp=(), fallacies=(), soundness=(), negative=(), assertions=False):
    return {"credits": list(credits), "has_assertions": assertions,
            "opp_args": list(opp), "fallacies": list(fallacies),
            "soundness": list(soundness), "negative_arg_ids": set(negative)}


def _opp(credit, answerable=True, addressed=False):
    return {"credit": credit, "answerable": answerable, "addressed": addressed}


# --- construction quality -----------------------------------------------------
def test_single_airtight_argument_hits_the_ceiling():
    assert construction_quality([1.0]) == 1.0


def test_extra_weak_argument_never_dilutes():
    assert construction_quality([1.0, 0.35]) == 1.0     # max-over-k drops it
    assert construction_quality([1.0, 0.95]) > 0.98     # a good one still helps... capped
    assert construction_quality([0.9, 0.9, 0.9]) == pytest.approx(0.9)


def test_no_arguments_scores_zero_or_assertion_floor():
    assert compute_score(_own([]))["q"] == 0.0
    assert compute_score(_own([], assertions=True))["q"] == ASSERTION_FLOOR


# --- engagement ---------------------------------------------------------------
def test_engagement_zero_when_nothing_answerable():
    assert engagement_u([_opp(1.0, answerable=False)]) == 0.0


def test_engagement_full_when_everything_ignored():
    assert engagement_u([_opp(0.9), _opp(0.9)]) == 1.0


def test_attempted_rebuttal_counts_even_if_it_failed():
    assert engagement_u([_opp(0.9, addressed=True)]) == 0.0


def test_ignoring_their_best_costs_more_than_their_worst():
    strong_ignored = engagement_u([_opp(1.0), _opp(0.35, addressed=True)])
    weak_ignored = engagement_u([_opp(1.0, addressed=True), _opp(0.35)])
    assert strong_ignored > weak_ignored


# --- deductions + the calibration question ------------------------------------
def test_linked_fallacy_factor_applies_only_to_negative_arguments():
    fal = [{"severity": "high", "argument_id": "b-1"}]
    full = deductions(fal, [], set())                 # argument not negative
    linked = deductions(fal, [], {"b-1"})             # argument already invalid/weak
    assert full == 8.0
    assert linked == pytest.approx(8.0 * LINKED_FALLACY_FACTOR)


# --- the approved scenario table ------------------------------------------------
def test_scenario_1_airtight_dodger_loses_to_decent_engager():
    a = compute_score(_own([1.0], opp=[_opp(0.9), _opp(0.9)]))          # ignores both
    b = compute_score(_own([0.9, 0.9, 0.9, 0.35],
                           opp=[_opp(1.0, addressed=True)]))            # engaged, failed
    assert a["score"] == 75.0
    assert b["score"] == 90.0
    assert b["score"] > a["score"]


def test_scenario_2_airtight_engager_beats_decent_engager():
    a = compute_score(_own([1.0, 0.9], opp=[_opp(0.9, addressed=True),
                                            _opp(0.9, addressed=True)]))
    b = compute_score(_own([0.9, 0.9, 0.63], opp=[_opp(1.0, addressed=True)]))
    assert a["score"] == 100.0
    # The weakened third argument DROPS OUT under max-over-k (the approved
    # no-dilution rule), so B holds 90 — the review table's 86.1 was an
    # arithmetic slip. A (airtight + engaged) still wins.
    assert b["score"] == 90.0


def test_scenario_4_two_dodgers_quality_decides():
    a = compute_score(_own([1.0], opp=[_opp(0.9), _opp(0.9)]))
    b = compute_score(_own([0.9, 0.9, 0.9], opp=[_opp(1.0)]))
    assert (a["score"], b["score"]) == (75.0, 65.0)


def test_scenario_5_strawman_token_engagement_costs_both_ways():
    # The straw-man "rebuttal" doesn't count as addressing (U stays 1.0) AND
    # the linked card deducts. addressed=False models the exclusion rule.
    a = compute_score(_own([1.0, 0.35], opp=[_opp(0.9), _opp(0.9)],
                           fallacies=[{"severity": "high", "argument_id": "a-2"}],
                           negative=["a-2"]))
    assert a["score"] == pytest.approx(100 - 25 - 8 * LINKED_FALLACY_FACTOR)
