"""The Verdict-v2 judge: extract once → evaluate four ways → merge → score.

Pipeline (all temp 0, all Flash):
  Phase A  2 extraction calls (backend/extraction.py) → canonical argument map
  Phase B  4 evaluation probes (2×2 label mapping × claims order) judging the
           SAME map: per-argument verdicts, soundness findings, fallacies
           (linked to arguments), extraction audits, the 5 axes, holistic winner
  repair   ≥2 probes flagging the same extraction gap → ONE re-extract+re-probe
  merge    deterministic consensus over shared argument ids (server code)
  score    درجة الحجاج (backend/scoring.py): quality-dominant Q, engagement
           duty U, rule-derived deductions — winner, margins, tiers from it;
           the 5 axes survive as a demoted strip AND as a cross-check (axes
           disagreeing with the structured score in sign forces متقاربة)
  synthesis one call narrating the ALREADY-merged verdict (cannot re-judge)

Safeguards carried whole from v1: the judge never sees clock time or real
names (turns t1..tN, speakers «أ»/«ب», names injected post-generation into
display text only); every displayed quote is verbatim-validated and anchored
to playable audio server-side; confidence is computed from ensemble behavior,
never self-reported; answerability rules are mechanical.
"""
from __future__ import annotations

import logging
import statistics
from concurrent.futures import ThreadPoolExecutor
from math import ceil
from typing import Optional

from . import config
from . import state as S
from .arabic import strip_names
from .extraction import (ExtractionError, resolve_rebuts, run_extraction,
                         _side_spaces, _validate_quoted)
from .gemini import GeminiError, generate_json
from .prompts import PROBE_PROMPT, SYNTHESIS_PROMPT
from .schemas import (AXES, AXIS_NAMES_AR, EMOTIONAL_FALLACIES, FALLACY_DEFS_AR,
                      FALLACY_NAMES, FALLACY_TYPES, SEVERITIES, SOUNDNESS_NAMES,
                      SOUNDNESS_TYPES, probe_schema, synthesis_schema)
from .scoring import CREDIT, SURVIVAL, compute_score
from .store import get_store

log = logging.getLogger("hakam.judge")

# Audio-proof padding (bias early — starting late is what breaks trust).
PREROLL_S = 1.5
POSTROLL_S = 1.0

# Tier thresholds on the درجة الحجاج scale (Gate-3 priors).
MARGIN_FORCED_CLOSE = 3.0
MARGIN_HIGH = 7.0
SPREAD_HIGH_MAX = 15.0      # axes spread still feeds instability
SPREAD_CLOSE_MIN = 25.0
MARGIN_BANDS = ((15.0, "decisive"), (7.0, "clear"), (3.0, "narrow"))

MAX_FALLACY_CARDS_PER_SPEAKER = 3

# (label_for_real_a/b, presentation order of labels) — 2×2, balanced by design.
PROBE_MATRIX = (
    ({"a": "a", "b": "b"}, "ab"),
    ({"a": "a", "b": "b"}, "ba"),
    ({"a": "b", "b": "a"}, "ab"),
    ({"a": "b", "b": "a"}, "ba"),
)

LABEL_AR = {"a": "أ", "b": "ب"}
_SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}
_EFFECT_ORDER = ["defeated", "weakened", "unaffected"]
_DEDUCTIVE_VERDICTS = {"valid", "invalid"}
_INDUCTIVE_VERDICTS = {"strong", "weak"}


# --------------------------------------------------------------------------
# Transcript / map views (shared with extraction via lazy imports there)
# --------------------------------------------------------------------------
def _turn_infos(room: dict) -> list:
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


def _valid_segids(room: dict) -> dict:
    out = {}
    for info in _turn_infos(room):
        out[info["tid"]] = {_seg_id(info["tid"], s["i"])
                            for s in _segments_ok(info["entry"])}
    return out


def _label_id(real_id: str, mapping: dict) -> str:
    side, n = real_id.split("-")
    return f"{LABEL_AR[mapping[side]]}-{n}"


def _render_map(maps: dict, mapping: dict, order: str) -> "tuple[str, dict]":
    """Argument map as Arabic text for a probe, ids per THIS probe's labels.
    Returns (text, label_id -> real_id)."""
    label_to_real = {v: k for k, v in mapping.items()}
    trans = {}
    blocks = []
    for label in (["a", "b"] if order == "ab" else ["b", "a"]):
        real = label_to_real[label]
        m = maps[real]
        lines = [f"حجج المتحدث «{LABEL_AR[label]}»:"]
        if not m["arguments"]:
            lines.append("  (لم تُستخرج حجج بنيوية)")
        for arg in m["arguments"]:
            lid = _label_id(arg["id"], mapping)
            trans[lid] = arg["id"]
            cls = arg["classification"]
            head = (f"[{lid}] ({'رئيسية' if arg['weight'] == 'primary' else 'فرعية'}"
                    f"، {'استنباطي' if cls['type'] == 'deductive' else 'استقرائي'}"
                    f"{'، تصنيف تقريبي' if cls['tentative'] else ''})")
            if arg.get("rebuts"):
                head += f" — ترد على {_label_id(arg['rebuts']['target_id'], mapping)}"
            lines.append(head)
            lines.append(f"  النتيجة {arg['conclusion']['segment_ids']}: «{arg['conclusion']['quote']}»")
            for p in arg["premises"]:
                ext = " [مقدمة خارجية]" if p["external"] else ""
                lines.append(f"  مقدمة {p['segment_ids']}: «{p['quote']}»{ext}")
            for ip in arg["implicit_premises"]:
                lines.append(f"  مقدمة مضمرة (من المحلل، غير منطوقة): {ip['text_ar']}")
        for u in m["unsupported_assertions"]:
            lines.append(f"  رأي بلا مقدمات {u['segment_ids']}: «{u['quote']}»")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks), trans


# --------------------------------------------------------------------------
# Probe execution + mechanical validation
# --------------------------------------------------------------------------
def _clean_probe(raw: dict, room: dict, mapping: dict, trans: dict,
                 maps: dict) -> Optional[dict]:
    """Validate one probe output; unmap labels/ids -> real. None if the core
    (axes/winner/evals) is unusable; bad items are struck individually."""
    label_to_real = {v: k for k, v in mapping.items()}
    spaces = _side_spaces(room)
    try:
        axes = {}
        for label in ("a", "b"):
            per = {}
            for ax in AXES:
                per[ax] = max(0, min(100, int(raw["axes"][label][ax]["score"])))
            axes[label_to_real[label]] = per
        holistic = label_to_real[raw["winner"]]
    except (KeyError, TypeError, ValueError):
        return None

    evals = {}
    for ev in raw.get("argument_evals") or []:
        rid = trans.get(ev.get("argument_id"))
        if rid is None or rid in evals:
            continue
        v = ev.get("verdict")
        if v not in _DEDUCTIVE_VERDICTS | _INDUCTIVE_VERDICTS:
            continue
        evals[rid] = {
            "verdict": v,
            "failure_point_ar": (ev.get("failure_point_ar") or "").strip(),
            "classification_agree": bool(ev.get("classification_agree", True)),
            "alt_classification": ev.get("alt_classification"),
            "rebuttal_effect": ev.get("rebuttal_effect"),
        }

    soundness = []
    for s in raw.get("soundness") or []:
        try:
            speaker = label_to_real[s["speaker"]]
            if s["type"] not in SOUNDNESS_TYPES:
                continue
            quotes = []
            for q in s.get("quotes") or []:
                v = _validate_quoted(q, spaces[speaker])
                if v is not None:
                    quotes.append(v)
            need = 2 if s["type"] == "self_contradiction" else 1
            if len(quotes) < need:
                continue
            soundness.append({
                "speaker": speaker, "type": s["type"], "quotes": quotes[:2],
                "argument_id": trans.get(s.get("argument_id")),
                "explanation_ar": (s.get("explanation_ar") or "").strip(),
                "segment_ids": [i for q in quotes[:2] for i in q["segment_ids"]],
            })
        except (KeyError, TypeError):
            continue

    fallacies = []
    for f in raw.get("fallacies") or []:
        try:
            speaker = label_to_real[f["speaker"]]
            v = _validate_quoted({"quote": f.get("quote"),
                                  "segment_ids": f.get("segment_ids")}, spaces[speaker])
            if (f["fallacy_type"] in FALLACY_TYPES and v is not None
                    and f.get("severity") in SEVERITIES):
                fallacies.append({
                    "speaker": speaker, "turn": v["turn"],
                    "segment_ids": v["segment_ids"], "quote": v["quote"],
                    "explanation_ar": (f.get("explanation_ar") or "").strip(),
                    "type": f["fallacy_type"], "severity": f["severity"],
                    "argument_id": trans.get(f.get("argument_id")),
                })
        except (KeyError, TypeError):
            continue

    issues = []
    for i in raw.get("extraction_issues") or []:
        ids = [x for x in (i.get("segment_ids") or []) if isinstance(x, str)]
        if i.get("kind") in ("missed_argument", "misread_argument") and ids:
            issues.append({"kind": i["kind"], "segment_ids": ids,
                           "note_ar": (i.get("note_ar") or "").strip()})

    return {"axes": axes, "evals": evals, "soundness": soundness,
            "fallacies": fallacies, "issues": issues, "holistic": holistic,
            "confidence": raw.get("confidence", "medium")}


def _run_probe(room: dict, maps: dict, mapping: dict, order: str) -> Optional[dict]:
    turn_ids = [i["tid"] for i in _turn_infos(room)]
    names = _names(room)
    map_text, trans = _render_map(maps, mapping, order)
    prompt = PROBE_PROMPT.format(
        topic=strip_names(room.get("topic", ""), names),
        claims_block=claims_block(room, mapping, order),
        transcript=transcript_view(room, mapping),
        argument_map=map_text,
        fallacy_list=_fallacy_list_text(),
    )
    schema = probe_schema(turn_ids, sorted(trans.keys()), order)
    for _ in range(2):
        try:
            raw = generate_json(prompt, schema,
                                thinking_budget=config.JUDGE_THINKING_BUDGET)
        except GeminiError as e:
            log.warning("probe call failed: %s", e)
            continue
        cleaned = _clean_probe(raw, room, mapping, trans, maps)
        if cleaned is not None:
            return cleaned
    return None


# --------------------------------------------------------------------------
# Merge v2 (deterministic; unit-tested directly)
# --------------------------------------------------------------------------
def _cluster(items: list, key_fn) -> list:
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
               key=lambda s: _SEV_RANK[s])


def _family(cls_type: str) -> set:
    return _DEDUCTIVE_VERDICTS if cls_type == "deductive" else _INDUCTIVE_VERDICTS


def merge_arg_evals(probes: list, maps: dict) -> dict:
    """Per real argument id -> consensus {classification, tentative, verdict,
    failure_point_ar, rebuttal_effect}. 'contested' when <3 probes agree."""
    out = {}
    for side in ("a", "b"):
        for arg in maps[side]["arguments"]:
            aid = arg["id"]
            votes = [p["evals"][aid] for p in probes if aid in p["evals"]]
            cls = arg["classification"]["type"]
            tentative = arg["classification"]["tentative"]
            # Classification override: >=3/4 disagree -> adopt majority alt.
            disagree = [v["alt_classification"] for v in votes
                        if not v["classification_agree"]
                        and v.get("alt_classification") in ("deductive", "inductive")
                        and v["alt_classification"] != cls]
            if len(disagree) >= max(1, ceil(0.75 * len(probes))):
                cls = max(set(disagree), key=disagree.count)
                tentative = True
            elif disagree:
                tentative = True
            fam = _family(cls)
            fam_votes = [v for v in votes if v["verdict"] in fam]
            counts = {}
            for v in fam_votes:
                counts[v["verdict"]] = counts.get(v["verdict"], 0) + 1
            threshold = max(1, ceil(0.75 * len(probes)))
            verdict, failure = "contested", ""
            for cand, n in sorted(counts.items()):
                if n >= threshold:
                    verdict = cand
                    failure = next((v["failure_point_ar"] for v in fam_votes
                                    if v["verdict"] == cand and v["failure_point_ar"]), "")
                    break
            effect = None
            if arg.get("rebuts"):
                effs = sorted(
                    (_EFFECT_ORDER.index(v["rebuttal_effect"]) for v in votes
                     if v.get("rebuttal_effect") in _EFFECT_ORDER))
                effect = _EFFECT_ORDER[effs[len(effs) // 2]] if effs else "unaffected"
            out[aid] = {"classification": cls, "tentative": tentative,
                        "verdict": verdict, "failure_point_ar": failure,
                        "rebuttal_effect": effect,
                        "votes": len(votes), "contested": verdict == "contested"}
    return out


def merge_findings(probes: list) -> "tuple[list, list]":
    """(consensus fallacies, consensus soundness) at >=ceil(0.75·valid)."""
    threshold = max(1, ceil(0.75 * len(probes)))
    fal = [
        {**c["item"], "severity": _modal_severity(c["severities"]),
         "found_by": len(c["probes"])}
        for c in _cluster([(i, f) for i, p in enumerate(probes) for f in p["fallacies"]],
                          key_fn=lambda f: (f["speaker"], f["type"], f.get("argument_id")))
        if len(c["probes"]) >= threshold
    ]
    snd = [
        {**c["item"], "found_by": len(c["probes"])}
        for c in _cluster([(i, s) for i, p in enumerate(probes) for s in p["soundness"]],
                          key_fn=lambda s: (s["speaker"], s["type"], s.get("argument_id")))
        if len(c["probes"]) >= threshold
    ]
    return fal, snd


def collect_audit_flags(probes: list) -> list:
    """Extraction issues >=2 probes agree on (kind + segment overlap)."""
    flagged = []
    for c in _cluster([(i, f) for i, p in enumerate(probes) for f in p["issues"]],
                      key_fn=lambda f: f["kind"]):
        if len(c["probes"]) >= 2:
            flagged.append(c["item"])
    return flagged


# --------------------------------------------------------------------------
# Scoring inputs (map + consensus -> scoring.py)
# --------------------------------------------------------------------------
def _severity_by_linkage(fallacy: dict, maps: dict) -> str:
    aid = fallacy.get("argument_id")
    if not aid:
        return "low"
    for side in ("a", "b"):
        for arg in maps[side]["arguments"]:
            if arg["id"] == aid:
                return "high" if arg["weight"] == "primary" else "medium"
    return "low"


def _strawman_rebuttal_ids(fallacies: list) -> set:
    return {f["argument_id"] for f in fallacies
            if f["type"] == "straw_man" and f.get("argument_id")}


def _score_inputs(side: str, maps: dict, evals: dict, fallacies: list,
                  soundness: list) -> dict:
    other = "b" if side == "a" else "a"
    strawman = _strawman_rebuttal_ids(fallacies)

    # Worst rebuttal effect suffered per target argument.
    suffered = {}
    for arg in maps[other]["arguments"]:
        if arg.get("rebuts") and arg["id"] not in strawman:
            ev = evals.get(arg["id"]) or {}
            eff = ev.get("rebuttal_effect")
            if eff in ("defeated", "weakened"):
                tid = arg["rebuts"]["target_id"]
                worst = suffered.get(tid, "unaffected")
                if _EFFECT_ORDER.index(eff) < _EFFECT_ORDER.index(worst):
                    suffered[tid] = eff

    credits, negative = [], set()
    for arg in maps[side]["arguments"]:
        ev = evals.get(arg["id"]) or {"verdict": "contested"}
        base = CREDIT.get(ev["verdict"], 0.5)
        credits.append(base * SURVIVAL.get(suffered.get(arg["id"], "unaffected"), 1.0))
        if ev["verdict"] in ("invalid", "weak"):
            negative.add(arg["id"])

    # Engagement duty: which answerable opponent arguments did we address?
    my_rebuts = {a["rebuts"]["target_id"] for a in maps[side]["arguments"]
                 if a.get("rebuts") and a["id"] not in strawman}
    opp_args = []
    for arg in maps[other]["arguments"]:
        ev = evals.get(arg["id"]) or {"verdict": "contested"}
        opp_args.append({"credit": CREDIT.get(ev["verdict"], 0.5),
                         "answerable": bool(arg.get("answerable")),
                         "addressed": arg["id"] in my_rebuts})

    return {
        "credits": credits,
        "has_assertions": bool(maps[side]["unsupported_assertions"]),
        "opp_args": opp_args,
        "fallacies": [f for f in fallacies if f["speaker"] == side],
        "soundness": [s for s in soundness if s["speaker"] == side],
        "negative_arg_ids": negative,
    }


def _probe_structured_winner(probe: dict, maps: dict) -> Optional[str]:
    """One probe's own winner vote via the same scoring function."""
    scores = {}
    for side in ("a", "b"):
        # rule-derived severities on the probe's own items
        fal = [{**f, "severity": _severity_by_linkage(f, maps)}
               for f in probe["fallacies"]]
        scores[side] = compute_score(_score_inputs(
            side, maps, probe["evals"], fal, probe["soundness"]))["score"]
    if scores["a"] > scores["b"]:
        return "a"
    if scores["b"] > scores["a"]:
        return "b"
    return None


# --------------------------------------------------------------------------
# Axes (retained: strip display + cross-check), anchors, emotionality
# --------------------------------------------------------------------------
def _inapplicable_axes(room: dict) -> set:
    out = set()
    infos = _turn_infos(room)
    for side in ("a", "b"):
        own = [i["index"] for i in infos if i["real"] == side]
        opp = [i["index"] for i in infos if i["real"] != side]
        if not own or not opp or max(own) < min(opp):
            out.add((side, "rebuttal"))
    return out


def merge_axes(probes: list, inapplicable: set) -> "tuple[dict, float]":
    scores, spread = {}, 0.0
    for side in ("a", "b"):
        scores[side] = {}
        for ax in AXES:
            if (side, ax) in inapplicable:
                scores[side][ax] = None
                continue
            vals = [p["axes"][side][ax] for p in probes]
            scores[side][ax] = int(round(statistics.median(vals)))
            spread = max(spread, max(vals) - min(vals))
    return scores, spread


def _axes_lean(axes_scores: dict) -> Optional[str]:
    tot = {s: statistics.mean(v for v in axes_scores[s].values() if v is not None)
           for s in ("a", "b")}
    if abs(tot["a"] - tot["b"]) < 1e-9:
        return None
    return "a" if tot["a"] > tot["b"] else "b"


def _anchor(room: dict, segment_ids: list) -> Optional[dict]:
    """Padded playback window for validated segment ids (single turn)."""
    if not segment_ids:
        return None
    tid = segment_ids[0].split("-")[0]
    info = next((i for i in _turn_infos(room) if i["tid"] == tid), None)
    if info is None:
        return None
    idx = {int(s.split("-")[1]) for s in segment_ids}
    segs = [s for s in _segments_ok(info["entry"]) if s["i"] in idx]
    if not segs:
        return None
    duration = info["entry"].get("duration_s") or segs[-1]["end_s"]
    return {"turn": info["entry"]["turn"],
            "start_s": round(max(0.0, min(s["start_s"] for s in segs) - PREROLL_S), 2),
            "end_s": round(min(duration, max(s["end_s"] for s in segs) + POSTROLL_S), 2)}


def _emotionality(axes_scores: dict, fallacies: list) -> dict:
    out = {}
    for side in ("a", "b"):
        emo = sum(1 for f in fallacies
                  if f["speaker"] == side and f["type"] in EMOTIONAL_FALLACIES)
        comp = axes_scores[side].get("composure") or 50
        out[side] = max(0, min(100, 100 - comp + 5 * min(2, emo)))
    return out


# --------------------------------------------------------------------------
# Tier decision
# --------------------------------------------------------------------------
def decide_tier(votes: list, margin: float, score_winner: Optional[str],
                axes_lean: Optional[str], spread: float, contested: int,
                incoherent: int, audits: int, repaired: bool,
                valid_probes: int) -> "tuple[str, Optional[str]]":
    top_vote, top_count = None, 0
    for cand in ("a", "b"):
        if votes.count(cand) > top_count:
            top_vote, top_count = cand, votes.count(cand)
    has_majority = top_count > valid_probes / 2
    agree = has_majority and score_winner is not None and top_vote == score_winner
    axes_conflict = (axes_lean is not None and score_winner is not None
                     and axes_lean != score_winner and margin < MARGIN_HIGH)

    if (valid_probes < 3 or margin < MARGIN_FORCED_CLOSE or not agree
            or spread > SPREAD_CLOSE_MIN or axes_conflict):
        return "close", None
    if (top_count == valid_probes == 4 and margin >= MARGIN_HIGH
            and spread <= SPREAD_HIGH_MAX and incoherent == 0
            and contested == 0 and audits == 0 and not repaired):
        return "high", score_winner
    return "medium", score_winner


# --------------------------------------------------------------------------
# Synthesis (narrate-only, unchanged contract)
# --------------------------------------------------------------------------
def _deanonymize_display(text: str, room: dict) -> str:
    if not text:
        return text
    names = {s: room["debaters"][s].get("name")
             or ("الطرف الأول" if s == "a" else "الطرف الثاني") for s in ("a", "b")}
    for label, side in (("أ", "a"), ("ب", "b")):
        for pat in (f"المتحدث «{label}»", f"المتحدث ({label})", f"المتحدث {label}",
                    f"المتناظر «{label}»"):
            text = text.replace(pat, names[side])
    return text


_FALLBACK_REASONING = {
    "close": "جاء أداء الطرفين متقاربًا إلى حدّ لا يمكن معه الجزم بمتفوّق، إذ لم يثبت فائز عند تدقيق الحكم بترتيبات مختلفة.",
    "medium": "رجحت كفة الفائز بعد موازنة بناء الحجج والرد عليها.",
    "high": "تفوق الفائز بوضوح في بناء حججه والرد على حجج خصمه.",
}

_VERDICT_AR = {"valid": "سليم البناء", "invalid": "مختل البناء",
               "strong": "حجة قوية", "weak": "حجة ضعيفة", "contested": "تقييم متقارب"}
_EFFECT_AR = {"defeated": "أسقطتها", "weakened": "أضعفتها", "unaffected": "لم تؤثر"}


def _results_block(maps: dict, arg_results: dict, fallacies: list,
                   soundness: list, scores: dict, winner: Optional[str]) -> str:
    lines = []
    if winner is None:
        lines.append("النتيجة: متقاربة — لا فائز محسوم.")
    else:
        lines.append(f"الفائز: المتحدث «{LABEL_AR[winner]}» "
                     f"({scores['a']['score']:.0f} مقابل {scores['b']['score']:.0f}).")
    for side in ("a", "b"):
        lines.append(f"درجة حجاج المتحدث «{LABEL_AR[side]}»: {scores[side]['score']:.0f}")
        for arg in maps[side]["arguments"]:
            r = arg_results[arg["id"]]
            line = (f"- حجة «{arg['conclusion']['quote'][:60]}»: "
                    f"{_VERDICT_AR[r['verdict']]}")
            if r["failure_point_ar"]:
                line += f" — {r['failure_point_ar']}"
            if arg.get("rebuts") and r["rebuttal_effect"]:
                line += f" (ردٌّ {_EFFECT_AR[r['rebuttal_effect']]})"
            if arg.get("unanswered"):
                line += " (بقيت بلا رد)"
            lines.append(line)
    for f in fallacies:
        lines.append(f"مغالطة على «{LABEL_AR[f['speaker']]}»: {FALLACY_NAMES[f['type']][0]} — «{f['quote'][:60]}»")
    for s in soundness:
        lines.append(f"خلل تماسك على «{LABEL_AR[s['speaker']]}»: {SOUNDNESS_NAMES[s['type']]}")
    return "\n".join(lines)


def _run_synthesis(room: dict, results_block: str, tier: str) -> dict:
    turn_ids = [i["tid"] for i in _turn_infos(room)]
    names = _names(room)
    mapping = {"a": "a", "b": "b"}
    prompt = SYNTHESIS_PROMPT.format(
        topic=strip_names(room.get("topic", ""), names),
        claims_block=claims_block(room, mapping, "ab"),
        results_block=results_block,
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
                          "segment_ids": ids, "audio": _anchor(room, ids)}
        profiles = raw.get("profiles") or {}
        for side in ("a", "b"):
            p = profiles.get(side) or {}
            if not all((p.get("strongest_ar"), p.get("weakest_ar"), p.get("tip_ar"))):
                raise GeminiError("incomplete synthesis profiles")
        reasoning = (raw.get("reasoning_ar") or "").strip() or _FALLBACK_REASONING[tier]
        return {"key_moment": key_moment, "profiles": profiles, "reasoning_ar": reasoning}
    except GeminiError as e:
        log.warning("synthesis failed, using fallback narrative: %s", e)
        return {"key_moment": None, "profiles": None,
                "reasoning_ar": _FALLBACK_REASONING[tier]}


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------
def _needs_transcript(t: dict) -> bool:
    return bool(t.get("audio_uri")) and not t.get("forfeited") \
        and (t.get("transcript") or {}).get("status") != "ok"


def _display_map(room: dict, maps: dict, arg_results: dict) -> dict:
    out = {}
    for side in ("a", "b"):
        args = []
        for arg in maps[side]["arguments"]:
            r = arg_results[arg["id"]]
            args.append({
                "id": arg["id"], "weight": arg["weight"],
                "classification": {"type": r["classification"], "tentative": r["tentative"]},
                "verdict": r["verdict"],
                "failure_point_ar": _deanonymize_display(r["failure_point_ar"], room),
                "conclusion": {"quote": arg["conclusion"]["quote"],
                               "audio": _anchor(room, arg["conclusion"]["segment_ids"])},
                "premises": [
                    {"quote": p["quote"], "external": p["external"],
                     "external_claim_ar": p["external_claim_ar"],
                     "audio": _anchor(room, p["segment_ids"])}
                    for p in arg["premises"]
                ],
                "implicit_premises": [{"text_ar": ip["text_ar"]}
                                      for ip in arg["implicit_premises"]],
                "rebuts": ({"target_id": arg["rebuts"]["target_id"],
                            "effect": r["rebuttal_effect"]}
                           if arg.get("rebuts") else None),
                "unanswered": bool(arg.get("unanswered")),
            })
        out[side] = {
            "arguments": args,
            "unsupported_assertions": [
                {"quote": u["quote"], "audio": _anchor(room, u["segment_ids"])}
                for u in maps[side]["unsupported_assertions"]],
            "orphan_premises": [
                {"quote": o["quote"], "audio": _anchor(room, o["segment_ids"])}
                for o in maps[side]["orphan_premises"]],
        }
    return out


def _external_claims(maps: dict) -> list:
    out = []
    for side in ("a", "b"):
        for arg in maps[side]["arguments"]:
            for p in arg["premises"]:
                if p["external"]:
                    out.append({"speaker": side, "argument_id": arg["id"],
                                "claim_ar": p["external_claim_ar"], "quote": p["quote"],
                                "segment_ids": p["segment_ids"]})
    return out


def build_verdict(room: dict) -> dict:
    # Transcript preflight (unchanged from v1): wait for the queue, then retry.
    import time
    deadline = time.time() + 25
    while time.time() < deadline and any(
            (t.get("transcript") or {}).get("status") == "pending"
            for t in room["turns"] if _needs_transcript(t)):
        time.sleep(2)
        room = get_store().get(room["code"]) or room
    from .transcribe import transcribe_turn
    retried = False
    for t in room["turns"]:
        if _needs_transcript(t):
            transcribe_turn(room["code"], t["turn"])
            retried = True
    if retried:
        room = get_store().get(room["code"]) or room

    # Phase A: extraction (2 parallel calls), Phase B: probes, one repair round.
    def _extract(notes=""):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futs = {s: pool.submit(run_extraction, room, s, notes) for s in ("a", "b")}
            out = {}
            for s, f in futs.items():
                try:
                    out[s] = f.result()
                except ExtractionError as e:
                    log.warning("extraction empty for %s: %s", s, e)
                    out[s] = {"side": s, "arguments": [],
                              "unsupported_assertions": [], "orphan_premises": []}
        return resolve_rebuts(out, room)

    def _probe_all(maps):
        with ThreadPoolExecutor(max_workers=len(PROBE_MATRIX)) as pool:
            futs = [pool.submit(_run_probe, room, maps, m, o) for m, o in PROBE_MATRIX]
            return [f.result() for f in futs if f.result() is not None]

    maps = _extract()
    probes = _probe_all(maps)
    if not probes:
        raise GeminiError("no valid judge probes")

    repaired = False
    audit = collect_audit_flags(probes)
    if audit:
        notes = "\n".join(f"- {a['kind']}: مقاطع {a['segment_ids']} — {a['note_ar']}"
                          for a in audit)
        log.info("extraction repair round triggered: %d flags", len(audit))
        maps = _extract(notes)
        probes = _probe_all(maps) or probes
        repaired = True
        audit = collect_audit_flags(probes)

    # Merge.
    arg_results = merge_arg_evals(probes, maps)
    fallacies, soundness = merge_findings(probes)
    for f in fallacies:  # severity is rule-derived from linkage
        f["severity"] = _severity_by_linkage(f, maps)
    axes_scores, axes_spread = merge_axes(probes, _inapplicable_axes(room))

    # Scores (consensus) + per-probe winner votes.
    scores = {s: compute_score(_score_inputs(s, maps,
              {k: v for k, v in arg_results.items()}, fallacies, soundness))
              for s in ("a", "b")}
    margin = abs(scores["a"]["score"] - scores["b"]["score"])
    score_winner = ("a" if scores["a"]["score"] > scores["b"]["score"]
                    else "b" if scores["b"]["score"] > scores["a"]["score"] else None)
    probe_winners = [_probe_structured_winner(p, maps) for p in probes]
    votes = [w for w in probe_winners if w]
    incoherent = sum(1 for p, w in zip(probes, probe_winners)
                     if w and p["holistic"] != w)
    contested = sum(1 for r in arg_results.values() if r["contested"])

    tier, winner = decide_tier(
        votes, margin, score_winner, _axes_lean(axes_scores), axes_spread,
        contested, incoherent, len(audit), repaired, len(probes))
    band = None
    if winner is not None:
        band = next((b for lim, b in MARGIN_BANDS if margin >= lim), "narrow")

    # Fallacy cards: anchor + cap (validation already guaranteed quotes).
    cards = []
    for f in sorted(fallacies, key=lambda f: (-_SEV_RANK[f["severity"]], f["turn"])):
        name_ar, name_en = FALLACY_NAMES[f["type"]]
        cards.append({**{k: f[k] for k in ("speaker", "type", "quote", "turn",
                                           "segment_ids", "severity", "argument_id")},
                      "name_ar": name_ar, "name_en": name_en,
                      "explanation_ar": _deanonymize_display(f["explanation_ar"], room),
                      "audio": _anchor(room, f["segment_ids"])})
    seen = {"a": 0, "b": 0}
    capped = []
    for c in cards:
        seen[c["speaker"]] += 1
        if seen[c["speaker"]] <= MAX_FALLACY_CARDS_PER_SPEAKER:
            capped.append(c)
    cards = capped

    soundness_cards = [{
        "speaker": s["speaker"], "type": s["type"],
        "name_ar": SOUNDNESS_NAMES[s["type"]], "argument_id": s.get("argument_id"),
        "explanation_ar": _deanonymize_display(s["explanation_ar"], room),
        "quotes": [{"quote": q["quote"], "audio": _anchor(room, q["segment_ids"])}
                   for q in s["quotes"]],
    } for s in soundness]

    results = _results_block(maps, arg_results, fallacies, soundness, scores, winner)
    narrative = _run_synthesis(room, results, tier)
    narrative["reasoning_ar"] = _deanonymize_display(narrative["reasoning_ar"], room)
    if narrative["key_moment"]:
        narrative["key_moment"]["description_ar"] = _deanonymize_display(
            narrative["key_moment"]["description_ar"], room)
    if narrative["profiles"]:
        for side in ("a", "b"):
            p = narrative["profiles"][side]
            for k in ("strongest_ar", "weakest_ar", "tip_ar"):
                p[k] = _deanonymize_display(p[k], room)

    return {
        "schema_version": 2,
        "tier": tier,
        "winner": winner,
        "margin": {"value": round(margin, 1), "band": band},
        "score": {s: scores[s]["score"] for s in ("a", "b")},
        "score_breakdown": scores,
        "analysis": _display_map(room, maps, arg_results),
        "soundness": soundness_cards,
        "external_claims": _external_claims(maps),
        "fallacies": cards,
        "scores": axes_scores,           # the demoted strip (+ v1 fallback shape)
        "emotionality": _emotionality(axes_scores, fallacies),
        "key_moment": narrative["key_moment"],
        "profiles": narrative["profiles"],
        "reasoning_ar": narrative["reasoning_ar"],
        "diagnostics": {
            "probes_valid": len(probes), "votes": votes, "incoherent_probes": incoherent,
            "contested_args": contested, "audit_flags": len(audit),
            "repaired": repaired, "axis_spread_max": round(axes_spread, 1),
            "model": config.GEMINI_MODEL,
        },
        "created_at": S.now_utc().isoformat(),
    }


def run_judging(code: str) -> dict:
    """Lease-guarded judging: claim -> build -> write. Returns the room."""
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
    except Exception as e:
        log.exception("judging failed for room %s", code)
        return store.update(code, lambda r: S.fail_judging(r, str(e)[:200], S.now_utc()))
