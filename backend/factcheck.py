"""Grounded fact verification of external claims (فحص الوقائع, Verdict v2.2).

The extractor flags premises that lean on facts outside the debate
(`external: true` + a distilled `external_claim_ar`). This module verifies
each DISTINCT claim with one Google-Search-grounded Flash call, in parallel,
and returns verdicts with source receipts. The judge runs it concurrently
with the probe ensemble, so a verdict gains facts at near-zero wall clock.

Design rules (user decision 2026-07-06, overturning the v2 tabula-rasa lock):
- FACTS ONLY: opinions, values, predictions, and normative questions are
  structurally `unverifiable` (checkable=false in the schema, re-checked here).
- Punishing verdicts need receipts: «contradicted» requires ≥2 distinct
  source domains, «partially» ≥1 — else DEMOTED to unverifiable (×1.0).
  Sources come exclusively from the response's grounding metadata; a URL the
  model writes into its own JSON is never trusted.
- Fail-open: any error/timeout → unverifiable. A verdict must never be
  delayed or blocked because search failed.
- The probes never see these results: structure judgment stays fact-blind and
  the factors apply deterministically in scoring (Gate-3 stability).
- Callers pass name-stripped text (anonymization is server code — the judge
  strips before calling; this module never sees the room doc).
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from . import config
from .arabic import normalize
from .gemini import GeminiError, generate_grounded_json
from .prompts import FACTCHECK_PROMPT
from .schemas import FACTCHECK_SCHEMA

log = logging.getLogger("hakam.factcheck")

VERDICTS = ("supported", "partially", "contradicted", "unverifiable")
VERDICT_AR = {"supported": "أكدته المصادر", "partially": "صحيح جزئيًا",
              "contradicted": "خالف المصادر", "unverifiable": "تعذّر التحقق"}
MAX_SOURCES = 4

_UNVERIFIABLE = {"verdict": "unverifiable", "explanation_ar": "", "sources": []}


def claim_key(claim_ar: str) -> str:
    """Dedup key: the same fact cited twice is verified (and billed) once."""
    return normalize(claim_ar or "")


def collect_claims(maps: dict) -> list:
    """Argument maps -> deduped claim worklist (capped at FACTCHECK_CAP):
    [{key, claim_ar, quote, segment_ids, side, argument_ids}]. `side` is the
    FIRST citer; argument_ids lists every argument leaning on the claim."""
    out, by_key = [], {}
    for side in ("a", "b"):
        for arg in maps[side]["arguments"]:
            for p in arg["premises"]:
                claim = (p.get("external_claim_ar") or "").strip()
                if not p.get("external") or not claim:
                    continue
                k = claim_key(claim)
                if not k:
                    continue
                item = by_key.get(k)
                if item is None:
                    if len(out) >= config.FACTCHECK_CAP:
                        log.warning("factcheck: cap %d reached — claim skipped",
                                    config.FACTCHECK_CAP)
                        continue
                    item = {"key": k, "claim_ar": claim, "quote": p["quote"],
                            "segment_ids": list(p.get("segment_ids") or []),
                            "side": side, "argument_ids": []}
                    by_key[k] = item
                    out.append(item)
                if arg["id"] not in item["argument_ids"]:
                    item["argument_ids"].append(arg["id"])
    return out


def _domains(sources: list) -> set:
    # Grounding chunk titles are the source domains ('officialdata.org').
    return {(s.get("title") or "").strip().lower()
            for s in sources if (s.get("title") or "").strip()}


# Appended when a punishing verdict arrives with no search receipts: the
# model answered from memory (grounding fires only when it CHOOSES to search),
# and a sourceless ruling is void — one retry demands the actual search.
_SEARCH_NUDGE = ("\n\nملاحظة إلزامية: أصدرتَ في محاولة سابقة حكمًا دون نتائج "
                 "بحث مرفقة فأُلغي. استعمل أداة البحث الآن فعليًا واذكر ما "
                 "وجدتَه في المصادر — أو احكم unverifiable.")


def _verify_one(topic: str, item: dict) -> dict:
    prompt = FACTCHECK_PROMPT.format(topic=topic, claim=item["claim_ar"],
                                     quote=item["quote"])
    demoted = None
    for attempt in (0, 1):
        try:
            raw, sources = generate_grounded_json(
                prompt + (_SEARCH_NUDGE if attempt else ""),
                FACTCHECK_SCHEMA,
                thinking_budget=config.FACTCHECK_THINKING_BUDGET)
        except GeminiError as e:
            log.warning("factcheck: verification errored (fail-open): %s", e)
            return dict(_UNVERIFIABLE, demoted="error")

        verdict = raw.get("verdict") if raw.get("verdict") in VERDICTS else "unverifiable"
        if not raw.get("checkable"):
            verdict = "unverifiable"
        sources = [{"title": (s.get("title") or "")[:120], "uri": s["uri"]}
                   for s in sources if s.get("uri")][:MAX_SOURCES]
        floor = {"contradicted": 2, "partially": 1}.get(verdict)
        if not floor or len(_domains(sources)) >= floor:
            break
        if attempt == 0:
            log.info("factcheck: «%s» without receipts — retrying with a "
                     "forced-search nudge", verdict)
            continue
        log.warning("factcheck: «%s» demoted to unverifiable — %d source "
                    "domain(s), needs %d", verdict, len(_domains(sources)), floor)
        verdict, demoted = "unverifiable", "receipts"
    return {"verdict": verdict,
            "explanation_ar": (raw.get("explanation_ar") or "").strip()[:500],
            "sources": sources, "demoted": demoted}


def verify_claims(topic: str, claims: list, cache: Optional[dict] = None) -> dict:
    """Verify a claim worklist -> {key: result}. `cache` persists across the
    judge's extraction-repair round so no claim is verified (billed) twice.
    Disabled or errored -> unverifiable: scoring treats it as ×1.0."""
    cache = cache if cache is not None else {}
    todo = [c for c in claims if c["key"] not in cache]
    if todo and config.FACTCHECK_ENABLED:
        with ThreadPoolExecutor(max_workers=min(8, len(todo))) as pool:
            futs = {c["key"]: pool.submit(_verify_one, topic, c) for c in todo}
            for k, f in futs.items():
                try:
                    cache[k] = f.result()
                except Exception as e:  # belt-and-braces: never block a verdict
                    log.warning("factcheck: worker crashed (fail-open): %s", e)
                    cache[k] = dict(_UNVERIFIABLE, demoted="error")
    for c in claims:
        cache.setdefault(c["key"], dict(_UNVERIFIABLE, demoted="disabled"))
    return cache
