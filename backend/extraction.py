"""Argument-structure extraction (Verdict v2, Phase A).

One Flash call PER debater, in parallel, each with the target labeled «أ» and
the target's claim presented first — extraction is label- and order-invariant
by construction, so position bias has no comparative judgment to act on. The
probes (Phase B) receive the same canonical map, which is what makes consensus
over structured output tractable: they vote on shared argument ids instead of
aligning four independent parse trees.

Everything the model returns passes deterministic validation before anything
downstream sees it:
- every quoted conclusion/premise must anchor (verbatim/fuzzy) inside ONE turn
  OWNED by the target debater — Section 1 is receipts, so an unanchorable
  quote is a validation failure, never a missing play button;
- implicit premises are text-only BY SCHEMA SHAPE (no field can hold ids);
- the argument cap and the exactly-one-primary rule are normalized here;
- `rebuts_segments` cross-references are resolved to opponent argument ids by
  segment overlap + temporal order (the two extraction calls run in parallel
  and never see each other's maps — the server draws the links).
"""
from __future__ import annotations

import logging
from typing import Optional

from . import config
from .arabic import find_span
from .gemini import generate_json
from .prompts import EXTRACTION_PROMPT
from .schemas import ARG_CAP, EXTRACTION_SCHEMA

log = logging.getLogger("hakam.extraction")


class ExtractionError(Exception):
    """The extractor produced nothing usable for this debater after a retry."""


# --------------------------------------------------------------------------
# Helpers over the room's segment space
# --------------------------------------------------------------------------
def _seg_order(seg_id: str) -> "tuple[int, int]":
    # "t3-04" -> (3, 4); malformed ids sort last and fail ownership anyway.
    try:
        tid, i = seg_id.split("-")
        return (int(tid[1:]), int(i))
    except (ValueError, IndexError):
        return (10 ** 6, 10 ** 6)


def _turn_of(seg_id: str) -> str:
    return seg_id.split("-")[0]


def _side_spaces(room: dict) -> dict:
    """Per side: valid segment ids, segments-by-tid, and owned turn indices."""
    from .judge import _segments_ok, _seg_id, _turn_infos
    spaces = {"a": {"ids": set(), "by_tid": {}, "turn_idx": set()},
              "b": {"ids": set(), "by_tid": {}, "turn_idx": set()}}
    for info in _turn_infos(room):
        side = info["real"]
        segs = _segments_ok(info["entry"])
        # A turn is an answer OPPORTUNITY even if it was forfeited — squandering
        # the slot doesn't excuse leaving a point unanswered (same rule the
        # judge's answerability check has always used).
        spaces[side]["turn_idx"].add(info["index"] + 1)  # tN numbering
        if segs:
            spaces[side]["by_tid"][info["tid"]] = segs
            spaces[side]["ids"] |= {_seg_id(info["tid"], s["i"]) for s in segs}
    return spaces


def _validate_quoted(item: dict, space: dict) -> Optional[dict]:
    """A quoted unit is valid iff its ids live in ONE target-owned turn and the
    quote anchors in that turn's segments. Returns {quote, segment_ids, turn}."""
    quote = (item.get("quote") or "").strip()
    ids = [i for i in (item.get("segment_ids") or []) if i in space["ids"]]
    if not quote or not ids:
        return None
    tids = {_turn_of(i) for i in ids}
    if len(tids) != 1:
        return None
    tid = tids.pop()
    if find_span(quote, space["by_tid"].get(tid, [])) is None:
        return None
    return {"quote": quote, "segment_ids": sorted(ids, key=_seg_order), "turn": tid}


# --------------------------------------------------------------------------
# Validation + normalization
# --------------------------------------------------------------------------
def validate_extraction(raw: dict, room: dict, side: str) -> dict:
    """Model output -> cleaned canonical map for `side` ('a'|'b').
    Drops invalid items; raises nothing (callers decide on emptiness)."""
    space = _side_spaces(room)[side]
    opp_space = _side_spaces(room)["b" if side == "a" else "a"]

    arguments = []
    for arg in (raw.get("arguments") or [])[: ARG_CAP + 2]:
        conclusion = _validate_quoted(arg.get("conclusion") or {}, space)
        if conclusion is None:
            log.warning("extraction[%s]: argument dropped (bad conclusion)", side)
            continue
        premises = []
        for p in arg.get("premises") or []:
            v = _validate_quoted(p, space)
            if v is None:
                log.warning("extraction[%s]: premise dropped (unanchorable)", side)
                continue
            external = bool(p.get("external"))
            claim = (p.get("external_claim_ar") or "").strip()
            if external and not claim:
                external = False  # tag without substance -> untag, log
                log.warning("extraction[%s]: external tag without claim text", side)
            premises.append({**v, "external": external,
                             "external_claim_ar": claim if external else ""})
        implicit = [
            {"text_ar": ip["text_ar"].strip(), "why_needed_ar": (ip.get("why_needed_ar") or "").strip()}
            for ip in (arg.get("implicit_premises") or []) if (ip.get("text_ar") or "").strip()
        ]
        cls = arg.get("classification") or {}
        if cls.get("type") not in ("deductive", "inductive"):
            log.warning("extraction[%s]: argument dropped (no classification)", side)
            continue
        rebuts = sorted({i for i in (arg.get("rebuts_segments") or [])
                         if i in opp_space["ids"]}, key=_seg_order)
        arguments.append({
            "weight": arg.get("weight") if arg.get("weight") in ("primary", "secondary") else "secondary",
            "conclusion": conclusion,
            "premises": premises,
            "implicit_premises": implicit,
            "classification": {"type": cls["type"], "tentative": bool(cls.get("tentative")),
                               "rationale_ar": (cls.get("rationale_ar") or "").strip()},
            "rebuts_segments": rebuts,
        })

    if len(arguments) > ARG_CAP:
        log.warning("extraction[%s]: cap exceeded (%d), truncating", side, len(arguments))
        arguments = arguments[:ARG_CAP]
    # Exactly one primary: promote the first if none, demote extras.
    primaries = [i for i, a in enumerate(arguments) if a["weight"] == "primary"]
    if arguments and not primaries:
        arguments[0]["weight"] = "primary"
    for i in primaries[1:]:
        arguments[i]["weight"] = "secondary"

    def _plain(items):
        out = []
        for it in items or []:
            v = _validate_quoted(it, space)
            if v is not None:
                out.append(v)
        return out

    return {
        "side": side,
        "arguments": arguments,
        "unsupported_assertions": _plain(raw.get("unsupported_assertions")),
        "orphan_premises": _plain(raw.get("orphan_premises")),
    }


def _assign_ids(cleaned: dict) -> dict:
    for n, arg in enumerate(cleaned["arguments"], start=1):
        arg["id"] = f"{cleaned['side']}-{n}"
    return cleaned


# --------------------------------------------------------------------------
# Rebuttal resolution + answerability (server-drawn cross-links)
# --------------------------------------------------------------------------
def _arg_seg_ids(arg: dict) -> set:
    ids = set(arg["conclusion"]["segment_ids"])
    for p in arg["premises"]:
        ids |= set(p["segment_ids"])
    return ids


def resolve_rebuts(maps: dict, room: dict) -> dict:
    """maps: {'a': map_a, 'b': map_b}. Annotates every argument with
    `rebuts` ({target_id} | None) and `unanswered` (bool). Deterministic."""
    spaces = _side_spaces(room)
    for side, other in (("a", "b"), ("b", "a")):
        for arg in maps[side]["arguments"]:
            arg["rebuts"] = None
            cited = set(arg.get("rebuts_segments") or [])
            if not cited:
                continue
            own_first = min((_seg_order(i) for i in _arg_seg_ids(arg)), default=(0, 0))
            best, best_key = None, (0, 0, 0)
            for t in maps[other]["arguments"]:
                t_ids = _arg_seg_ids(t)
                overlap = len(cited & t_ids)
                if overlap == 0:
                    continue
                concl_overlap = len(cited & set(t["conclusion"]["segment_ids"]))
                # temporal rule: the rebuttal must come after the target
                t_last = max(_seg_order(i) for i in t_ids)
                if own_first <= t_last:
                    continue
                key = (overlap, concl_overlap, -int(t["id"].split("-")[1]))
                if key > best_key:
                    best, best_key = t, key
            if best is not None:
                arg["rebuts"] = {"target_id": best["id"]}
            else:
                log.info("extraction[%s]: rebuts_segments matched no answerable target", side)

    # Unanswered badges: answerable (opponent owned a later turn) + unrebutted.
    for side, other in (("a", "b"), ("b", "a")):
        rebutted = {a["rebuts"]["target_id"] for a in maps[other]["arguments"] if a.get("rebuts")}
        opp_turns = spaces[other]["turn_idx"]
        for arg in maps[side]["arguments"]:
            raised_turn = max(_seg_order(i)[0] for i in _arg_seg_ids(arg))
            answerable = any(t > raised_turn for t in opp_turns)
            arg["unanswered"] = bool(answerable and arg["id"] not in rebutted)
    return maps


# --------------------------------------------------------------------------
# Entry point (one debater; judge runs two of these in parallel)
# --------------------------------------------------------------------------
def run_extraction(room: dict, side: str, repair_notes: str = "") -> dict:
    """Extract `side`'s argument map. Target is ALWAYS «أ» in its own call.
    One retry if the model produced arguments but none survived validation;
    raises ExtractionError only when nothing usable exists after that."""
    from .judge import claims_block, transcript_view, strip_names, _names
    mapping = {side: "a", ("b" if side == "a" else "a"): "b"}
    names = _names(room)
    prompt = EXTRACTION_PROMPT.format(
        topic=strip_names(room.get("topic", ""), names),
        claims_block=claims_block(room, mapping, "ab"),
        transcript=transcript_view(room, mapping),
        repair_notes=f"\nملاحظات وجب تداركها في هذا التحليل:\n{repair_notes}\n" if repair_notes else "",
    )
    for attempt in range(2):
        extra = "" if attempt == 0 else (
            "\nملاحظة: كل اقتباس يجب أن يكون حرفيًا من كلام المتحدث «أ» نفسه"
            " وبمعرفات مقاطعه الصحيحة من مداخلاته هو.")
        raw = generate_json(prompt + extra, EXTRACTION_SCHEMA,
                            thinking_budget=config.EXTRACT_THINKING_BUDGET,
                            retries=0 if attempt else 1)
        cleaned = validate_extraction(raw, room, side)
        if cleaned["arguments"] or cleaned["unsupported_assertions"] or not raw.get("arguments"):
            return _assign_ids(cleaned)
        # else: the model asserted arguments existed but none validated — retry once
    raise ExtractionError(f"no argument survived validation for side {side}")
