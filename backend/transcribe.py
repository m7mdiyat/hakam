"""Per-turn transcription: canonical m4a -> timestamped sentence segments.

The segments stored here are the app's single time authority (design: the judge
never sees or emits clock time — it cites segment IDs, which resolve back to
these start/end values). So this module is strict about time sanity: a transcript
whose timestamps fail validation is retried once and otherwise marked failed —
a failed transcript only costs the audio-proof buttons, a wrong one costs trust.

Runs on a queue worker (Cloud Tasks -> /api/internal/transcribe) or a local dev
thread; both call transcribe_turn(), which is idempotent per turn.
"""
from __future__ import annotations

import re
from typing import Optional

from . import config
from . import state as S
from .gemini import GeminiError, audio_part, generate_json
from .prompts import TRANSCRIBE_PROMPT
from .storage import get_storage
from .store import get_store

# Segment ceiling the prompt asks for, enforced leniently (model output that
# slightly exceeds it is fine; it's a segmentation hint, not a validity rule).
TRANSCRIBE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "segments": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "start": {"type": "STRING", "description": "بداية المقطع بصيغة MM:SS"},
                    "end": {"type": "STRING", "description": "نهاية المقطع بصيغة MM:SS"},
                    "text": {"type": "STRING", "description": "النص حرفيًا باللهجة كما نُطقت"},
                },
                "required": ["start", "end", "text"],
                "propertyOrdering": ["start", "end", "text"],
            },
        },
    },
    "required": ["segments"],
}

_TS = re.compile(r"^\s*(?:(\d{1,2}):)?(\d{1,3}):(\d{2})(?:\.(\d{1,3}))?\s*$")


def parse_ts(value: str) -> Optional[float]:
    """'MM:SS' (or 'H:MM:SS' / trailing '.mmm') -> seconds. None if unparseable."""
    m = _TS.match(value or "")
    if not m:
        return None
    h, mm, ss, frac = m.groups()
    seconds = int(mm) * 60 + int(ss) + (int(h) * 3600 if h else 0)
    if frac:
        seconds += int(frac) / (10 ** len(frac))
    return float(seconds)


class SegmentError(ValueError):
    """Model segments failed time/shape validation (triggers one retry)."""


def normalize_segments(raw: list, duration_s: Optional[float]) -> list:
    """Model output -> validated [{i, start_s, end_s, text}].

    Hard failures (unparseable/negative/out-of-range/non-monotonic starts) raise
    SegmentError; benign sloppiness (tiny overlaps, end slightly past EOF) is
    clamped. Ordering by start time is enforced, not assumed.
    """
    segs = []
    for item in raw or []:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        start = parse_ts(item.get("start", ""))
        end = parse_ts(item.get("end", ""))
        if start is None or end is None:
            raise SegmentError(f"unparseable timestamp: {item.get('start')!r}/{item.get('end')!r}")
        if end < start:
            start, end = end, start  # swapped pair — salvageable
        segs.append({"start_s": start, "end_s": end, "text": text})

    if not segs:
        raise SegmentError("no non-empty segments")
    segs.sort(key=lambda s: (s["start_s"], s["end_s"]))

    if duration_s:
        limit = duration_s + 2.0  # MM:SS quantization + model slack
        if segs[-1]["start_s"] > limit or segs[0]["start_s"] < 0:
            raise SegmentError("segment outside the audio duration")
        for s in segs:
            if s["start_s"] > limit:
                raise SegmentError("segment outside the audio duration")
            s["end_s"] = min(s["end_s"], duration_s)
            s["start_s"] = min(s["start_s"], s["end_s"])

    for i, s in enumerate(segs):
        s["i"] = i
    return segs


def _write_transcript(code: str, turn_key: str, transcript: dict) -> None:
    def mut(room: dict):
        for t in room["turns"]:
            if t["turn"] == turn_key:
                existing = t.get("transcript") or {}
                transcript["attempts"] = int(existing.get("attempts", 0)) + 1
                t["transcript"] = transcript
                return
        raise LookupError(f"turn {turn_key} not found in room {code}")

    get_store().update(code, mut)


def transcribe_turn(code: str, turn_key: str) -> str:
    """Transcribe one recorded turn; returns final status ('ok'|'failed'|'skipped').

    Idempotent: a turn whose transcript is already ok is left alone (queue
    retries and double-enqueues are harmless).
    """
    room = get_store().get(code)
    if room is None:
        return "skipped"
    turn = next((t for t in room["turns"] if t["turn"] == turn_key), None)
    if turn is None or turn.get("forfeited") or not turn.get("audio_uri"):
        return "skipped"
    if (turn.get("transcript") or {}).get("status") == "ok":
        return "ok"

    uri = turn.get("audio_m4a_uri") or turn["audio_uri"]
    mime = "audio/mp4" if turn.get("audio_m4a_uri") else turn.get("content_type", "audio/mp4")
    audio = get_storage().read(uri)
    prompt = TRANSCRIBE_PROMPT.format(topic=room.get("topic", ""))

    last_err = None
    for attempt in range(2):  # one full retry on model/validation failure
        try:
            result = generate_json(
                prompt if attempt == 0 else prompt
                + "\nملاحظة: التزم بدقة بأزمنة البداية والنهاية ضمن مدة التسجيل وبترتيب زمني تصاعدي.",
                TRANSCRIBE_SCHEMA,
                parts=[audio_part(audio, mime)],
                thinking_budget=0,
                retries=0 if attempt else 1,
            )
            segments = normalize_segments(result.get("segments"), turn.get("duration_s"))
            _write_transcript(code, turn_key, {
                "status": "ok", "segments": segments, "model": config.GEMINI_MODEL,
            })
            return "ok"
        except (GeminiError, SegmentError) as e:
            last_err = e

    _write_transcript(code, turn_key, {
        "status": "failed", "segments": [], "model": config.GEMINI_MODEL,
        "error": str(last_err)[:200],
    })
    return "failed"
