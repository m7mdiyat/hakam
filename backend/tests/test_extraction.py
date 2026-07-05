"""Extraction stage (Verdict v2 Phase A): deterministic validation, cap and
primary normalization, and the server-drawn rebuts/unanswered cross-links."""
import pytest

from backend.extraction import (ExtractionError, resolve_rebuts, run_extraction,
                                validate_extraction)
from backend.state import new_room

# Fixture debate: a1 (A: two sentences), b1 (B: two), a2 (A: rebuttal), b2 (B).
TURN_TEXTS = {
    "turn_a1": ["التعليم عن بعد يوسع الفجوة بين الطلاب",
                "لأن جودة الإنترنت تتفاوت بين البيوت"],
    "turn_b1": ["بل يضيق الفجوة لأن الدروس المسجلة تعاد متى شئت",
                "والدعم الحكومي يغطي الأسر محدودة الدخل"],
    "turn_a2": ["الدروس المسجلة لا تكفي لأن الطالب الضعيف يحتاج متابعة مباشرة"],
    "turn_b2": ["وأختم بأن التجربة أثبتت نجاح التعليم عن بعد"],
}


def _room():
    room = new_room("EXTR01", "التعليم عن بعد", "tok")
    room["debaters"]["a"].update({"name": "أحمد", "claim": "يوسع الفجوة"})
    room["debaters"]["b"].update({"name": "سارة", "claim": "يضيق الفجوة"})
    for turn_key, texts in TURN_TEXTS.items():
        segs = [{"i": i, "start_s": 5.0 * i, "end_s": 5.0 * (i + 1), "text": t}
                for i, t in enumerate(texts)]
        room["turns"].append({
            "turn": turn_key, "debater": turn_key.split("_")[1][0],
            "audio_uri": "gs://x", "audio_m4a_uri": "gs://x", "content_type": "audio/mp4",
            "duration_ms": 10000, "duration_s": 5.0 * len(texts), "audio_stats": None,
            "forfeited": False, "created_at": room["created_at"],
            "transcript": {"status": "ok", "segments": segs, "attempts": 1},
        })
    room["state"] = "deliberating"
    return room


def _arg(concl_ids, concl_quote, weight="secondary", premises=(), rebuts=()):
    return {
        "rebuts_segments": list(rebuts),
        "conclusion": {"segment_ids": concl_ids, "quote": concl_quote},
        "premises": [{"segment_ids": ids, "quote": q, "external": ext,
                      "external_claim_ar": claim}
                     for ids, q, ext, claim in premises],
        "implicit_premises": [],
        "classification": {"rationale_ar": "س", "type": "inductive", "tentative": False},
        "weight": weight,
    }


A_ARG = _arg(["t1-00"], "التعليم عن بعد يوسع الفجوة بين الطلاب", weight="primary",
             premises=[(["t1-01"], "جودة الإنترنت تتفاوت بين البيوت", True,
                        "جودة الإنترنت تتفاوت بين مناطق البلاد")])


# --- validation --------------------------------------------------------------
def test_happy_path_validates_and_assigns_ids(monkeypatch):
    raw = {"arguments": [A_ARG], "unsupported_assertions": [], "orphan_premises": []}
    monkeypatch.setattr("backend.extraction.generate_json", lambda *a, **k: raw)
    m = run_extraction(_room(), "a")
    (arg,) = m["arguments"]
    assert arg["id"] == "a-1" and arg["weight"] == "primary"
    assert arg["conclusion"]["turn"] == "t1"
    assert arg["premises"][0]["external"] is True


def test_fabricated_quote_drops_argument():
    raw = {"arguments": [_arg(["t1-00"], "جملة لم تقال في المناظرة إطلاقا مطلقا")],
           "unsupported_assertions": [], "orphan_premises": []}
    assert validate_extraction(raw, _room(), "a")["arguments"] == []


def test_opponent_owned_segment_drops_argument():
    raw = {"arguments": [_arg(["t2-00"], "بل يضيق الفجوة لأن الدروس المسجلة تعاد متى شئت")],
           "unsupported_assertions": [], "orphan_premises": []}
    # Real quote — but it's B's speech; A's extractor may not claim it.
    assert validate_extraction(raw, _room(), "a")["arguments"] == []


def test_external_tag_without_claim_is_untagged():
    arg = _arg(["t1-00"], "التعليم عن بعد يوسع الفجوة بين الطلاب",
               premises=[(["t1-01"], "جودة الإنترنت تتفاوت بين البيوت", True, "")])
    cleaned = validate_extraction({"arguments": [arg], "unsupported_assertions": [],
                                   "orphan_premises": []}, _room(), "a")
    assert cleaned["arguments"][0]["premises"][0]["external"] is False


def test_wrong_segment_citation_is_rewritten_from_the_quote():
    # Model quoted segment t1-01's words but cited t1-00: the stored ids must
    # follow the TEXT (they double as the audio-proof playback window).
    arg = _arg(["t1-00"], "لأن جودة الإنترنت تتفاوت بين البيوت")
    cleaned = validate_extraction({"arguments": [arg], "unsupported_assertions": [],
                                   "orphan_premises": []}, _room(), "a")
    assert cleaned["arguments"][0]["conclusion"]["segment_ids"] == ["t1-01"]


def test_wrong_turn_citation_is_rescued_by_text_search():
    # Cited A's later turn, but the quote lives in t1: recovered, not dropped.
    arg = _arg(["t3-00"], "التعليم عن بعد يوسع الفجوة بين الطلاب")
    cleaned = validate_extraction({"arguments": [arg], "unsupported_assertions": [],
                                   "orphan_premises": []}, _room(), "a")
    c = cleaned["arguments"][0]["conclusion"]
    assert c["turn"] == "t1" and c["segment_ids"] == ["t1-00"]


def test_cap_and_single_primary_normalization():
    args = [_arg(["t1-00"], "التعليم عن بعد يوسع الفجوة بين الطلاب", weight="primary")
            for _ in range(5)]
    cleaned = validate_extraction({"arguments": args, "unsupported_assertions": [],
                                   "orphan_premises": []}, _room(), "a")
    assert len(cleaned["arguments"]) == 4
    assert [a["weight"] for a in cleaned["arguments"]].count("primary") == 1


def test_retry_then_error_when_nothing_validates(monkeypatch):
    bad = {"arguments": [_arg(["t1-00"], "كلام مختلق تماما ليس من النص")],
           "unsupported_assertions": [], "orphan_premises": []}
    calls = []
    monkeypatch.setattr("backend.extraction.generate_json",
                        lambda *a, **k: (calls.append(1), bad)[1])
    with pytest.raises(ExtractionError):
        run_extraction(_room(), "a")
    assert len(calls) == 2  # one retry before giving up


def test_empty_map_is_legal_without_retry(monkeypatch):
    empty = {"arguments": [], "unsupported_assertions": [], "orphan_premises": []}
    calls = []
    monkeypatch.setattr("backend.extraction.generate_json",
                        lambda *a, **k: (calls.append(1), empty)[1])
    m = run_extraction(_room(), "a")
    assert m["arguments"] == [] and len(calls) == 1


# --- rebuts resolution + unanswered ------------------------------------------
def _maps():
    room = _room()
    map_a = validate_extraction({
        "arguments": [
            A_ARG,
            _arg(["t3-00"], "الدروس المسجلة لا تكفي لأن الطالب الضعيف يحتاج متابعة مباشرة",
                 rebuts=["t2-00"]),                      # rebuts B's recorded-lessons point
        ], "unsupported_assertions": [], "orphan_premises": []}, room, "a")
    map_b = validate_extraction({
        "arguments": [
            _arg(["t2-00"], "بل يضيق الفجوة لأن الدروس المسجلة تعاد متى شئت", weight="primary"),
            _arg(["t4-00"], "التجربة أثبتت نجاح التعليم عن بعد"),   # final turn: A can't answer
        ], "unsupported_assertions": [], "orphan_premises": []}, room, "b")
    from backend.extraction import _assign_ids
    return resolve_rebuts({"a": _assign_ids(map_a), "b": _assign_ids(map_b)}, room), room


def test_rebuts_resolves_by_overlap_with_temporal_rule():
    maps, _ = _maps()
    a1, a2 = maps["a"]["arguments"]
    assert a1["rebuts"] is None
    assert a2["rebuts"] == {"target_id": "b-1"}


def test_unanswered_badges_respect_answerability():
    maps, _ = _maps()
    b1, b2 = maps["b"]["arguments"]
    assert b1["unanswered"] is False       # rebutted by a-2
    assert b2["unanswered"] is False       # raised in the final turn: not answerable
    a1, a2 = maps["a"]["arguments"]
    assert a1["unanswered"] is True        # B had later turns and never engaged it
    assert a2["unanswered"] is True        # raised in a2; B's b2 came later, no rebuttal


def test_self_referencing_rebuts_are_stripped():
    room = _room()
    arg = _arg(["t3-00"], "الدروس المسجلة لا تكفي لأن الطالب الضعيف يحتاج متابعة مباشرة",
               rebuts=["t1-00"])           # points at A's own speech
    cleaned = validate_extraction({"arguments": [arg], "unsupported_assertions": [],
                                   "orphan_premises": []}, room, "a")
    assert cleaned["arguments"][0]["rebuts_segments"] == []


def test_temporal_violation_yields_null_rebuts():
    room = _room()
    from backend.extraction import _assign_ids
    # A's a1 argument claims to rebut B's b1 point — but a1 PRECEDES b1.
    map_a = _assign_ids(validate_extraction({
        "arguments": [_arg(["t1-00"], "التعليم عن بعد يوسع الفجوة بين الطلاب",
                           rebuts=["t2-00"])],
        "unsupported_assertions": [], "orphan_premises": []}, room, "a"))
    map_b = _assign_ids(validate_extraction({
        "arguments": [_arg(["t2-00"], "بل يضيق الفجوة لأن الدروس المسجلة تعاد متى شئت")],
        "unsupported_assertions": [], "orphan_premises": []}, room, "b"))
    maps = resolve_rebuts({"a": map_a, "b": map_b}, room)
    assert maps["a"]["arguments"][0]["rebuts"] is None
