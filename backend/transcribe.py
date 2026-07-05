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

import logging
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
    """Model output -> [{i, start_s, end_s, text}] in the model's own order.

    The TEXT is the payload — quotes anchor against the token stream and
    playback aligns over measured speech time — while model TIMES are
    quantized buckets whose clock drifts (a flawless 2-minute transcript
    once arrived stamped 6s past EOF and a hard range check threw the whole
    thing away — room PYYQWF). Times are therefore salvaged, never fatal:
    unparseable stamps borrow a neighbour's, values clamp into the audio
    and are made monotonic, and the ARRAY order (the model's transcription
    order) is trusted over sorting by fictional starts. The only hard
    failure left is a transcript with no text at all.
    """
    segs = []
    for item in raw or []:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        start = parse_ts(item.get("start", ""))
        end = parse_ts(item.get("end", ""))
        if start is None and end is None:
            start = end = segs[-1]["end_s"] if segs else 0.0
        elif start is None:
            start = end
        elif end is None:
            end = start
        if end < start:
            start, end = end, start  # swapped pair
        segs.append({"start_s": start, "end_s": end, "text": text})

    if not segs:
        raise SegmentError("no non-empty segments")

    prev = 0.0
    for i, s in enumerate(segs):
        s["start_s"] = max(prev, s["start_s"], 0.0)
        s["end_s"] = max(s["end_s"], s["start_s"])
        if duration_s:
            s["start_s"] = min(s["start_s"], duration_s)
            s["end_s"] = min(s["end_s"], duration_s)
        prev = s["start_s"]
        s["i"] = i
    return segs


def _write_transcript(code: str, turn_key: str, transcript: dict) -> None:
    def mut(room: dict):
        for t in room["turns"]:
            if t["turn"] == turn_key:
                existing = t.get("transcript") or {}
                transcript["attempts"] = int(existing.get("attempts", 0)) + 1
                t["transcript"] = transcript
                # ok or failed, the wait is over either way: if the next
                # turn's prep window is held on this transcript, open it.
                S.release_processing_hold(room, turn_key)
                return
        raise LookupError(f"turn {turn_key} not found in room {code}")

    get_store().update(code, mut)


# A transcript must reach at least (real speech end - this slack) or it gets
# one explicit continue-to-the-end retry; still short -> accepted but flagged
# "degraded" and logged (partial truth beats none, and the flag is honest).
COVERAGE_SLACK_S = 8.0

log = logging.getLogger("hakam.transcribe")


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

    # Defense in depth behind the upload speech gate: silent audio must never
    # reach the model — given silence + a topic it fabricates a transcript.
    stats = turn.get("audio_stats") or {}
    if stats and stats.get("max_db", 0.0) < config.SILENCE_GATE_DB:
        _write_transcript(code, turn_key, {
            "status": "failed", "segments": [], "model": config.GEMINI_MODEL,
            "error": "silent audio (gate)", "reason": "no_speech",
        })
        return "failed"

    uri = turn.get("audio_m4a_uri") or turn["audio_uri"]
    mime = "audio/mp4" if turn.get("audio_m4a_uri") else turn.get("content_type", "audio/mp4")
    audio = get_storage().read(uri)
    prompt = TRANSCRIBE_PROMPT.format(topic=room.get("topic", ""))
    speech_end = stats.get("speech_end_s")

    last_err = None
    short = False
    for attempt in range(2):  # one full retry on failure OR under-coverage
        try:
            extra = ""
            if attempt == 1:
                extra = "\nملاحظة: التزم بدقة بأزمنة البداية والنهاية ضمن مدة التسجيل وبترتيب زمني تصاعدي."
                if short and speech_end:
                    extra += (f"\nمهم جدًا: التسجيل يحتوي كلامًا حتى الثانية {speech_end:.0f}"
                              " تقريبًا — انسخ الكلام كاملًا حتى نهايته ولا تتوقف قبل ذلك.")
            result = generate_json(
                prompt + extra,
                TRANSCRIBE_SCHEMA,
                parts=[audio_part(audio, mime)],
                thinking_budget=0,
                retries=0 if attempt else 1,
            )
            segments = normalize_segments(result.get("segments"), turn.get("duration_s"))
            short = bool(speech_end) and segments[-1]["end_s"] < speech_end - COVERAGE_SLACK_S
            if short and attempt == 0:
                last_err = SegmentError(
                    f"coverage {segments[-1]['end_s']:.0f}s < speech end {speech_end:.0f}s")
                continue
            transcript = {"status": "ok", "segments": segments, "model": config.GEMINI_MODEL}
            if short:  # retried and still short: keep the partial, say so
                transcript["degraded"] = "tail_missing"
                log.warning("transcript for %s/%s still short after retry: ends %.0fs, speech %.0fs",
                            code, turn_key, segments[-1]["end_s"], speech_end)
            _write_transcript(code, turn_key, transcript)
            return "ok"
        except (GeminiError, SegmentError) as e:
            last_err = e

    failed = {"status": "failed", "segments": [], "model": config.GEMINI_MODEL,
              "error": str(last_err)[:200]}
    if isinstance(last_err, SegmentError) and "no non-empty segments" in str(last_err):
        # An honest empty, not a pipeline error: the model heard nothing to
        # transcribe (loud non-speech — rustling, static — passes the
        # amplitude gate; room XUXX7S recorded 27s of mic noise at −2 dB).
        # Clients use this to tell the debater it was their microphone.
        failed["reason"] = "no_speech"
    _write_transcript(code, turn_key, failed)
    return "failed"
