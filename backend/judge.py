"""The judge ensemble: 4 deterministic probes -> merge -> synthesis -> verdict.

Robustness design (judge model is Flash-class; safeguards are structural):
- 2x2 probe matrix: label mapping (which real debater is «أ») x presentation
  order, all at temperature 0. Every probe is a genuinely different prompt;
  disagreement between them is the uncertainty signal.
- Mechanically checkable rubric rules are enforced HERE in code (answerability
  of dropped points, quote anchoring, segment-id validity) — never trusted to
  the model.
- Scores merge by median; the winner needs a strict vote majority AND the
  score-derived winner to agree; fallacies need >=ceil(0.75*valid_probes)
  independent detections and a verbatim/fuzzy quote anchor, else no card.
- Displayed confidence is computed from ensemble behavior (votes, spread,
  incoherence); the model's self-reported confidence never gates anything.
- The synthesis call narrates the ALREADY-merged verdict (it cannot re-judge),
  so the story can never contradict the numbers.

The judge never sees clock time or real names: turns are t1..tN, speakers are
«أ»/«ب», and quotes anchor back to transcript segments server-side.
"""
from __future__ import annotations

import logging
import statistics
from concurrent.futures import ThreadPoolExecutor
from math import ceil
from typing import Optional

from . import config
from . import state as S
from .arabic import find_span, strip_names
from .gemini import GeminiError, generate_json
from .prompts import PROBE_PROMPT, SYNTHESIS_PROMPT
from .schemas import (AXES, EMOTIONAL_FALLACIES, FALLACY_DEFS_AR, FALLACY_NAMES,
                      FALLACY_TYPES, SEVERITIES, probe_schema, synthesis_schema)
from .store import get_store

log = logging.getLogger("hakam.judge")

# Audio-proof padding (bias early — starting late is what breaks trust).
# Placeholder values pending QA Gate 1's measured error distribution.
PREROLL_S = 1.5
POSTROLL_S = 1.0

# Tier thresholds (approved design; tunable against the eval corpus).
MARGIN_FORCED_CLOSE = 3.0   # below this, declaring a winner is false precision
MARGIN_HIGH = 7.0
SPREAD_HIGH_MAX = 15.0      # per-axis max-min across probes
SPREAD_CLOSE_MIN = 25.0
MARGIN_BANDS = ((15.0, "decisive"), (7.0, "clear"), (3.0, "narrow"))

MAX_FALLACY_CARDS_PER_SPEAKER = 3

# (label_for_real_a, presentation order of labels) — 2x2, balanced by design.
PROBE_MATRIX = (
    ({"a": "a", "b": "b"}, "ab"),
    ({"a": "a", "b": "b"}, "ba"),
    ({"a": "b", "b": "a"}, "ab"),
    ({"a": "b", "b": "a"}, "ba"),
)

LABEL_AR = {"a": "أ", "b": "ب"}
_SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}  # low=0 .. high=2


# --------------------------------------------------------------------------
# Transcript view construction
# --------------------------------------------------------------------------
def _turn_infos(room: dict) -> list:
    """[{tid, entry, index, real}] in chronological order (room turn list)."""
    return [{"tid": f"t{i + 1}", "entry": t, "index": i, "real": t["debater"]}
            for i, t in enumerate(room["turns"])]


def _names(room: dict) -> list:
    return [room["debaters"][s].get("name") or "" for s in ("a", "b")]


def _segments_ok(entry: dict) -> list:
    tr = entry.get("transcript") or {}
    return tr.get("segments", []) if tr.get("status") == "ok" else []


def _seg_id(tid: str, i: int) -> str:
    return f"{tid}-{i:02d}"


def transcript_view(room: dict, mapping: dict) -> str:
    """mapping: real side -> label. Lines like `[t1-00] المتحدث «أ»: ...`."""
    names = _names(room)
    lines = []
    for info in _turn_infos(room):
        label = LABEL_AR[mapping[info["real"]]]
        speaker = f"المتحدث «{label}»"
        entry = info["entry"]
        if entry.get("forfeited"):
            lines.append(f"[{info['tid']}] {speaker}: (لم يسجّل مداخلته)")
            continue
        segs = _segments_ok(entry)
        if not segs:
            lines.append(f"[{info['tid']}] {speaker}: (تعذّر نسخ هذه المداخلة)")
            continue
        for seg in segs:
            text = strip_names(seg["text"], names)
            lines.append(f"[{_seg_id(info['tid'], seg['i'])}] {speaker}: {text}")
    return "\n".join(lines)


def claims_block(room: dict, mapping: dict, order: str) -> str:
    names = _names(room)
    label_to_real = {v: k for k, v in mapping.items()}
    out = []
    for label in (["a", "b"] if order == "ab" else ["b", "a"]):
        claim = strip_names(room["debaters"][label_to_real[label]].get("claim") or "", names)
        out.append(f"دعوى المتحدث «{LABEL_AR[label]}»: {claim}")
    return "\n".join(out)


def _fallacy_list_text() -> str:
    return "\n".join(f"- {FALLACY_NAMES[t][0]} ({t}): {FALLACY_DEFS_AR[t]}"
                     for t in FALLACY_TYPES)


# --------------------------------------------------------------------------
# Probe execution + mechanical validation
# --------------------------------------------------------------------------
def _valid_segids(room: dict) -> dict:
    """tid -> set of valid segment ids."""
    out = {}
    for info in _turn_infos(room):
        out[info["tid"]] = {_seg_id(info["tid"], s["i"])
                            for s in _segments_ok(info["entry"])}
    return out


def _answerable(room: dict, dropper_real: str, raised_tid: str) -> bool:
    """A point only counts as dropped if the dropper had a later turn than the
    turn it was raised in — and it was raised by the opponent."""
    infos = {i["tid"]: i for i in _turn_infos(room)}
    raised = infos.get(raised_tid)
    if raised is None or raised["real"] == dropper_real:
        return False
    return any(i["real"] == dropper_real and i["index"] > raised["index"]
               for i in infos.values())


def _clean_probe(raw: dict, room: dict, mapping: dict) -> Optional[dict]:
    """Validate one probe output; unmap labels -> real sides.
    Returns None if the core (axes/winner) is unusable; strikes bad items."""
    label_to_real = {v: k for k, v in mapping.items()}
    try:
        axes = {}
        for label in ("a", "b"):
            per = {}
            for ax in AXES:
                score = int(raw["axes"][label][ax]["score"])
                per[ax] = max(0, min(100, score))
            axes[label_to_real[label]] = per
        holistic = label_to_real[raw["winner"]]
    except (KeyError, TypeError, ValueError):
        return None

    segids = _valid_segids(room)
    fallacies, dropped = [], []
    for f in raw.get("fallacies") or []:
        try:
            ids = [i for i in (f.get("segment_ids") or []) if i in segids.get(f["turn"], ())]
            if (f["fallacy_type"] in FALLACY_TYPES and f["speaker"] in ("a", "b")
                    and f.get("quote", "").strip() and ids
                    and f.get("severity") in SEVERITIES):
                fallacies.append({
                    "speaker": label_to_real[f["speaker"]], "turn": f["turn"],
                    "segment_ids": ids, "quote": f["quote"].strip(),
                    "explanation_ar": (f.get("explanation_ar") or "").strip(),
                    "type": f["fallacy_type"], "severity": f["severity"],
                })
        except (KeyError, TypeError):
            continue
    for d in raw.get("dropped_points") or []:
        try:
            dropper = label_to_real[d["speaker"]]
            ids = [i for i in (d.get("segment_ids") or []) if i in segids.get(d["raised_turn"], ())]
            if d.get("point_ar", "").strip() and ids and _answerable(room, dropper, d["raised_turn"]):
                dropped.append({"speaker": dropper, "raised_turn": d["raised_turn"],
                                "segment_ids": ids, "point_ar": d["point_ar"].strip()})
        except (KeyError, TypeError):
            continue

    totals = {s: sum(axes[s].values()) / len(AXES) for s in ("a", "b")}
    computed = ("a" if totals["a"] > totals["b"]
                else "b" if totals["b"] > totals["a"] else None)
    return {"axes": axes, "fallacies": fallacies, "dropped": dropped,
            "holistic": holistic, "computed": computed,
            "confidence": raw.get("confidence", "medium")}


def _run_probe(room: dict, mapping: dict, order: str) -> Optional[dict]:
    turn_ids = [i["tid"] for i in _turn_infos(room)]
    names = _names(room)
    prompt = PROBE_PROMPT.format(
        topic=strip_names(room.get("topic", ""), names),
        claims_block=claims_block(room, mapping, order),
        transcript=transcript_view(room, mapping),
        fallacy_list=_fallacy_list_text(),
    )
    schema = probe_schema(turn_ids, order)
    for _ in range(2):  # one full re-ask if the output fails validation
        try:
            raw = generate_json(prompt, schema,
                                thinking_budget=config.JUDGE_THINKING_BUDGET)
        except GeminiError as e:
            log.warning("probe call failed: %s", e)
            continue
        cleaned = _clean_probe(raw, room, mapping)
        if cleaned is not None:
            return cleaned
    return None


# --------------------------------------------------------------------------
# Merge (pure; unit-tested directly)
# --------------------------------------------------------------------------
def _cluster(items: list, key_fn) -> list:
    """Group per-probe items by key + overlapping segment ids.
    items: (probe_idx, item). Returns [{item, probes:set, severities:[...]}]."""
    clusters = []
    for probe_idx, item in items:
        placed = False
        for c in clusters:
            same_key = key_fn(c["item"]) == key_fn(item)
            overlap = set(c["item"]["segment_ids"]) & set(item["segment_ids"])
            if same_key and overlap:
                c["probes"].add(probe_idx)
                c["severities"].append(item.get("severity"))
                placed = True
                break
        if not placed:
            clusters.append({"item": dict(item), "probes": {probe_idx},
                             "severities": [item.get("severity")]})
    return clusters


def _modal_severity(sevs: list) -> str:
    sevs = [s for s in sevs if s in _SEV_RANK]
    if not sevs:
        return "medium"
    counts = {s: sevs.count(s) for s in set(sevs)}
    best = max(counts.values())
    return min((s for s, c in counts.items() if c == best),
               key=lambda s: _SEV_RANK[s])  # tie -> the milder severity


def merge_probes(probes: list, inapplicable: Optional[set] = None) -> dict:
    """Merge validated probes into scores + winner + tier + consensus lists.

    `inapplicable`: {(side, axis)} pairs the format made unscorable (e.g. the
    rebuttal axis for a debater who never had a turn after an opponent's —
    probes can only guess there, so their noise must not gate the tier).
    Excluded axes render as None and count toward nothing."""
    inapplicable = inapplicable or set()
    valid = len(probes)
    scores, spreads, spread = {}, {"a": {}, "b": {}}, 0.0
    for side in ("a", "b"):
        scores[side] = {}
        for ax in AXES:
            if (side, ax) in inapplicable:
                scores[side][ax] = None
                spreads[side][ax] = None
                continue
            vals = [p["axes"][side][ax] for p in probes]
            scores[side][ax] = int(round(statistics.median(vals)))
            spreads[side][ax] = max(vals) - min(vals)
            spread = max(spread, spreads[side][ax])

    totals = {
        s: statistics.mean(v for v in scores[s].values() if v is not None)
        for s in ("a", "b")
    }
    margin = abs(totals["a"] - totals["b"])
    score_winner = ("a" if totals["a"] > totals["b"]
                    else "b" if totals["b"] > totals["a"] else None)

    votes = [p["computed"] for p in probes if p["computed"]]
    top_vote, top_count = None, 0
    for cand in ("a", "b"):
        if votes.count(cand) > top_count:
            top_vote, top_count = cand, votes.count(cand)
    incoherent = sum(1 for p in probes if p["computed"] and p["holistic"] != p["computed"])

    unanimous = valid == 4 and top_count == 4
    has_majority = top_count > valid / 2
    agree = has_majority and score_winner is not None and top_vote == score_winner

    if (valid < 3 or margin < MARGIN_FORCED_CLOSE or spread > SPREAD_CLOSE_MIN
            or not agree):
        tier, winner = "close", None
    elif unanimous and margin >= MARGIN_HIGH and spread <= SPREAD_HIGH_MAX and incoherent == 0:
        tier, winner = "high", score_winner
    else:
        tier, winner = "medium", score_winner

    band = None
    if winner is not None:
        band = next((b for lim, b in MARGIN_BANDS if margin >= lim), "narrow")

    threshold = max(1, ceil(0.75 * valid)) if valid else 1
    fallacies = [
        {**c["item"], "severity": _modal_severity(c["severities"]),
         "found_by": len(c["probes"])}
        for c in _cluster([(i, f) for i, p in enumerate(probes) for f in p["fallacies"]],
                          key_fn=lambda f: (f["speaker"], f["type"], f["turn"]))
        if len(c["probes"]) >= threshold
    ]
    dropped = [
        {**c["item"], "found_by": len(c["probes"])}
        for c in _cluster([(i, d) for i, p in enumerate(probes) for d in p["dropped"]],
                          key_fn=lambda d: (d["speaker"], d["raised_turn"]))
        if len(c["probes"]) >= threshold
    ]

    return {
        "scores": scores, "margin": round(margin, 2), "margin_band": band,
        "winner": winner, "tier": tier,
        "fallacies": fallacies, "dropped_points": dropped,
        "diagnostics": {
            "probes_valid": valid, "votes": votes, "incoherent_probes": incoherent,
            "axis_spread_max": round(spread, 1), "axis_spreads": spreads,
            "consensus_threshold": threshold,
        },
    }


# --------------------------------------------------------------------------
# Anchors, emotionality, synthesis, assembly
# --------------------------------------------------------------------------
def _resolve_anchor(room: dict, tid: str, quote: str) -> Optional[dict]:
    info = next((i for i in _turn_infos(room) if i["tid"] == tid), None)
    if info is None:
        return None
    segs = _segments_ok(info["entry"])
    span = find_span(quote, segs)
    if span is None:
        return None
    first, last = span
    duration = info["entry"].get("duration_s") or segs[-1]["end_s"]
    start = next(s["start_s"] for s in segs if s["i"] == first)
    end = next(s["end_s"] for s in segs if s["i"] == last)
    return {"turn": info["entry"]["turn"],
            "start_s": round(max(0.0, start - PREROLL_S), 2),
            "end_s": round(min(duration, end + POSTROLL_S), 2)}


def _finalize_fallacies(room: dict, merged: dict) -> list:
    """Anchor quotes; a card whose quote can't be anchored is dropped entirely.
    Cap per speaker by severity, then chronology."""
    kept = []
    for f in merged["fallacies"]:
        anchor = _resolve_anchor(room, f["turn"], f["quote"])
        if anchor is None:
            log.warning("fallacy quote failed to anchor; dropping card (%s)", f["type"])
            continue
        name_ar, name_en = FALLACY_NAMES[f["type"]]
        kept.append({
            "speaker": f["speaker"], "type": f["type"],
            "name_ar": name_ar, "name_en": name_en,
            "quote": f["quote"], "turn": anchor["turn"],
            "segment_ids": f["segment_ids"], "severity": f["severity"],
            "explanation_ar": f["explanation_ar"], "audio": anchor,
        })
    kept.sort(key=lambda f: (-_SEV_RANK[f["severity"]], f["turn"]))
    out, seen = [], {"a": 0, "b": 0}
    for f in kept:
        if seen[f["speaker"]] < MAX_FALLACY_CARDS_PER_SPEAKER:
            seen[f["speaker"]] += 1
            out.append(f)
    return out


def _inapplicable_axes(room: dict) -> set:
    """Mechanically undecidable axes: rebuttal is unscorable for a debater who
    never had a turn after any opponent turn (nothing existed to rebut)."""
    out = set()
    infos = _turn_infos(room)
    for side in ("a", "b"):
        own = [i["index"] for i in infos if i["real"] == side]
        opp = [i["index"] for i in infos if i["real"] != side]
        if not own or not opp or max(own) < min(opp):
            out.add((side, "rebuttal"))
    return out


def _emotionality(scores: dict, fallacies: list) -> dict:
    out = {}
    for side in ("a", "b"):
        emo_hits = sum(1 for f in fallacies
                       if f["speaker"] == side and f["type"] in EMOTIONAL_FALLACIES)
        val = 100 - scores[side]["composure"] + 5 * min(2, emo_hits)
        out[side] = max(0, min(100, val))
    return out


def _results_block(merged: dict) -> str:
    lines = []
    if merged["winner"] is None:
        lines.append("النتيجة: متقاربة — لا فائز محسوم؛ ثبت تقارب الأداء عند تدقيق الحكم بترتيبات مختلفة.")
    else:
        lines.append(f"الفائز: المتحدث «{LABEL_AR[merged['winner']]}» بفارق {merged['margin']:.0f} نقطة.")
    from .schemas import AXIS_NAMES_AR
    for side in ("a", "b"):
        parts = "، ".join(
            f"{AXIS_NAMES_AR[ax]}: "
            + ("غير منطبق" if merged["scores"][side][ax] is None
               else str(merged["scores"][side][ax]))
            for ax in AXES)
        lines.append(f"درجات المتحدث «{LABEL_AR[side]}»: {parts}")
    for f in merged["fallacies"]:
        lines.append(f"مغالطة مرصودة على «{LABEL_AR[f['speaker']]}»: {FALLACY_NAMES[f['type']][0]} — «{f['quote']}»")
    for d in merged["dropped_points"]:
        lines.append(f"نقطة بلا رد (تركها «{LABEL_AR[d['speaker']]}»): {d['point_ar']}")
    return "\n".join(lines)


def _deanonymize_display(text: str, room: dict) -> str:
    """DISPLAY-ONLY, applied strictly AFTER all model calls: swap the synthesis
    narrative's «المتحدث «أ»» references for the real names humans see. The
    judge pipeline's inputs stay fully anonymized (strip_names / labels) — this
    touches nothing that is ever sent to a model, and never touches quotes."""
    if not text:
        return text
    names = {s: room["debaters"][s].get("name")
             or ("الطرف الأول" if s == "a" else "الطرف الثاني") for s in SIDES_REAL}
    for label, side in (("أ", "a"), ("ب", "b")):
        for pat in (f"المتحدث «{label}»", f"المتحدث ({label})", f"المتحدث {label}",
                    f"المتناظر «{label}»"):
            text = text.replace(pat, names[side])
    return text


SIDES_REAL = ("a", "b")


_FALLBACK_REASONING = {
    "close": "جاء أداء الطرفين متقاربًا إلى حدّ لا يمكن معه الجزم بمتفوّق، إذ لم يثبت فائز عند تدقيق الحكم بترتيبات مختلفة.",
    "medium": "رجحت كفة الفائز بهامش ضئيل بعد موازنة المعايير الخمسة.",
    "high": "تفوق الفائز بوضوح في مجمل معايير التقييم.",
}


def _run_synthesis(room: dict, merged: dict) -> dict:
    turn_ids = [i["tid"] for i in _turn_infos(room)]
    names = _names(room)
    mapping = {"a": "a", "b": "b"}  # canonical: real a = «أ»
    prompt = SYNTHESIS_PROMPT.format(
        topic=strip_names(room.get("topic", ""), names),
        claims_block=claims_block(room, mapping, "ab"),
        results_block=_results_block(merged),
        transcript=transcript_view(room, mapping),
    )
    try:
        raw = generate_json(prompt, synthesis_schema(turn_ids),
                            thinking_budget=config.SYNTH_THINKING_BUDGET)
        km = raw.get("key_moment") or {}
        key_moment = None
        info = next((i for i in _turn_infos(room) if i["tid"] == km.get("turn")), None)
        if info is not None and km.get("description_ar", "").strip():
            segids = _valid_segids(room).get(km["turn"], set())
            ids = [i for i in (km.get("segment_ids") or []) if i in segids]
            key_moment = {"turn": info["entry"]["turn"],
                          "description_ar": km["description_ar"].strip(),
                          "segment_ids": ids, "audio": None}
            if ids:
                segs = {s: None for s in ids}
                seg_is = sorted(int(s.split("-")[1]) for s in segs)
                seg_objs = [x for x in _segments_ok(info["entry"]) if x["i"] in seg_is]
                if seg_objs:
                    duration = info["entry"].get("duration_s") or seg_objs[-1]["end_s"]
                    key_moment["audio"] = {
                        "turn": info["entry"]["turn"],
                        "start_s": round(max(0.0, seg_objs[0]["start_s"] - PREROLL_S), 2),
                        "end_s": round(min(duration, seg_objs[-1]["end_s"] + POSTROLL_S), 2)}
        profiles = raw.get("profiles") or {}
        for side in ("a", "b"):
            p = profiles.get(side) or {}
            if not all((p.get("strongest_ar"), p.get("weakest_ar"), p.get("tip_ar"))):
                raise GeminiError("incomplete synthesis profiles")
        reasoning = (raw.get("reasoning_ar") or "").strip() or _FALLBACK_REASONING[merged["tier"]]
        return {"key_moment": key_moment, "profiles": profiles, "reasoning_ar": reasoning}
    except GeminiError as e:
        log.warning("synthesis failed, using fallback narrative: %s", e)
        return {"key_moment": None, "profiles": None,
                "reasoning_ar": _FALLBACK_REASONING[merged["tier"]]}


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------
def _needs_transcript(t: dict) -> bool:
    return bool(t.get("audio_uri")) and not t.get("forfeited") \
        and (t.get("transcript") or {}).get("status") != "ok"


def build_verdict(room: dict) -> dict:
    # Preflight. The final turn's queue transcription is usually still in
    # flight when judging starts (the same upload request triggers both), so
    # WAIT for pending transcripts briefly instead of re-transcribing in a
    # race with the queue worker (double model calls, last-writer-wins).
    import time
    deadline = time.time() + 25
    while time.time() < deadline and any(
            (t.get("transcript") or {}).get("status") == "pending"
            for t in room["turns"] if _needs_transcript(t)):
        time.sleep(2)
        room = get_store().get(room["code"]) or room

    # Last-chance inline transcription only for what the queue never finished.
    from .transcribe import transcribe_turn
    retried = False
    for t in room["turns"]:
        if _needs_transcript(t):
            transcribe_turn(room["code"], t["turn"])
            retried = True
    if retried:
        room = get_store().get(room["code"]) or room

    with ThreadPoolExecutor(max_workers=len(PROBE_MATRIX)) as pool:
        futures = [pool.submit(_run_probe, room, m, o) for m, o in PROBE_MATRIX]
        probes = [f.result() for f in futures]
    probes = [p for p in probes if p is not None]
    if not probes:
        raise GeminiError("no valid judge probes")

    merged = merge_probes(probes, inapplicable=_inapplicable_axes(room))
    fallacies = _finalize_fallacies(room, merged)
    narrative = _run_synthesis(room, merged)

    # Human-facing narrative gets real names (model I/O stayed anonymized).
    narrative["reasoning_ar"] = _deanonymize_display(narrative["reasoning_ar"], room)
    if narrative["key_moment"]:
        narrative["key_moment"]["description_ar"] = _deanonymize_display(
            narrative["key_moment"]["description_ar"], room)
    if narrative["profiles"]:
        for side in ("a", "b"):
            p = narrative["profiles"][side]
            for k in ("strongest_ar", "weakest_ar", "tip_ar"):
                p[k] = _deanonymize_display(p[k], room)

    turn_key_of = {i["tid"]: i["entry"]["turn"] for i in _turn_infos(room)}
    dropped = [{"speaker": d["speaker"], "point_ar": d["point_ar"],
                "raised_turn": turn_key_of.get(d["raised_turn"], d["raised_turn"]),
                "segment_ids": d["segment_ids"]}
               for d in merged["dropped_points"]]

    return {
        "tier": merged["tier"],
        "winner": merged["winner"],
        "margin": {"value": merged["margin"], "band": merged["margin_band"]},
        "scores": merged["scores"],
        "emotionality": _emotionality(merged["scores"], fallacies),
        "fallacies": fallacies,
        "dropped_points": dropped,
        "key_moment": narrative["key_moment"],
        "profiles": narrative["profiles"],
        "reasoning_ar": narrative["reasoning_ar"],
        "diagnostics": {**merged["diagnostics"], "model": config.GEMINI_MODEL},
        "created_at": S.now_utc().isoformat(),
    }


def run_judging(code: str) -> dict:
    """Lease-guarded judging: claim -> build -> write. Returns the room.
    Safe to call from any request that observes state == deliberating."""
    store = get_store()
    claimed = {}

    def claim(r):
        claimed["ok"] = S.begin_judging(r, S.now_utc())

    room = store.update(code, claim)
    if not claimed.get("ok"):
        return room
    try:
        verdict = build_verdict(room)
        return store.update(code, lambda r: S.finish_judging(r, verdict, S.now_utc()))
    except Exception as e:  # keep the room consistent; clients can retrigger
        log.exception("judging failed for room %s", code)
        return store.update(code, lambda r: S.fail_judging(r, str(e)[:200], S.now_utc()))
