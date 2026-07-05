"""Arabic text utilities for the judge pipeline.

Three jobs, all deterministic server-side code (never model output):
- normalize(): fold orthographic variation so "verbatim" comparisons survive
  tashkeel, hamza carriers, and punctuation differences between the transcriber
  and the judge quoting it.
- find_span(): anchor a judge-quoted phrase to the transcript segments it came
  from — exact match first, then a bounded fuzzy match. Returns None rather
  than guessing: a fallacy card whose quote can't be anchored loses its audio
  proof (or the card entirely) instead of pointing at the wrong 3 seconds.
- strip_names(): remove the debaters' first names from text shown to the judge
  (anonymization is server code, not a model instruction).
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Optional

_TASHKEEL = re.compile(r"[ً-ْٰـ]")  # harakat, dagger alif, tatweel
_ALIF = re.compile(r"[آأإٱ]")        # آ أ إ ٱ -> ا
# Keep Arabic letters, ASCII letters, digits (incl. Arabic-Indic) and spaces.
_DROP = re.compile(r"[^ء-ي٠-٩a-zA-Z0-9\s]")
_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    t = _TASHKEEL.sub("", text or "")
    t = _ALIF.sub("ا", t)
    t = t.replace("ؤ", "و")   # ؤ -> و
    t = t.replace("ئ", "ي")   # ئ -> ي
    t = t.replace("ى", "ي")   # ى -> ي
    t = t.replace("ة", "ه")   # ة -> ه
    t = _DROP.sub(" ", t)
    return _SPACES.sub(" ", t).strip().lower()


def tokens(text: str) -> list:
    return normalize(text).split()


# One divergent token in a 6-token quote scores ~0.83 on ordered-token
# SequenceMatcher — exactly the ASR-vs-judge drift the fuzzy tier exists for,
# so the bar sits just under it. Unrelated text scores far lower.
FUZZY_THRESHOLD = 0.80


def token_stream(segments: list) -> list:
    """A turn's segments -> [(normalized_token, segment_i)] in speech order."""
    return [(tok, seg["i"]) for seg in segments
            for tok in tokens(seg.get("text", ""))]


def _token_spans(quote: str, segments: list) -> list:
    """ALL candidate anchors for `quote` -> [(tok_start, tok_end_excl)].

    Every exact contiguous match; if none, the single best fuzzy window with
    SequenceMatcher ratio >= FUZZY_THRESHOLD (transcriber and judge may
    disagree on a particle or two). Empty list -> unanchorable.
    """
    q = tokens(quote)
    if not q:
        return []
    stream = token_stream(segments)
    if not stream:
        return []
    toks = [t for t, _ in stream]
    n, m = len(toks), len(q)

    spans = [(start, start + m) for start in range(n - m + 1)
             if toks[start:start + m] == q]
    if spans:
        return spans

    best_ratio, best_start = 0.0, -1
    for start in range(max(1, n - m + 1)):
        window = toks[start:start + m]
        ratio = SequenceMatcher(None, window, q).ratio()
        if ratio > best_ratio:
            best_ratio, best_start = ratio, start
    if best_ratio >= FUZZY_THRESHOLD and best_start >= 0:
        return [(best_start, min(best_start + m, n))]
    return []


def find_token_span(quote: str, segments: list,
                    near: Optional[set] = None) -> Optional["tuple[int, int]"]:
    """Anchor `quote` in a turn's token stream -> (tok_start, tok_end_excl).

    Short phrases repeat («هذا غير صحيح» can be said three times); anchoring
    the FIRST occurrence plays the wrong repetition. `near` — the segment
    indices the judge cited — disambiguates: prefer the occurrence whose
    segments overlap it, else the one nearest to it, else the first.
    """
    spans = _token_spans(quote, segments)
    if not spans:
        return None
    if len(spans) == 1 or not near:
        return spans[0]
    stream = token_stream(segments)

    def seg_range(span):
        return stream[span[0]][1], stream[span[1] - 1][1]

    def distance(span):
        lo, hi = seg_range(span)
        if any(lo <= i <= hi for i in near):
            return 0
        return min(abs(i - lo) if i < lo else abs(i - hi) for i in near)

    return min(spans, key=distance)


def find_span(quote: str, segments: list,
              near: Optional[set] = None) -> Optional["tuple[int, int]"]:
    """Anchor `quote` in a turn's segments -> (first_seg_i, last_seg_i) or None."""
    hit = find_token_span(quote, segments, near)
    if hit is None:
        return None
    stream = token_stream(segments)
    return stream[hit[0]][1], stream[hit[1] - 1][1]


def strip_names(text: str, names: list) -> str:
    """Replace exact-token occurrences of the debaters' first names (normalized
    comparison) with a neutral word. Keeps everything else verbatim."""
    targets = {normalize(n) for n in names if n and normalize(n)}
    if not targets:
        return text
    out = []
    for word in (text or "").split():
        # Compare without surrounding punctuation, replace the whole word.
        out.append("المتناظر"  # المتناظر
                   if normalize(word) in targets else word)
    return " ".join(out)
