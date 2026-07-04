"""Audio normalization via ffmpeg.

Every uploaded turn is transcoded once, at upload time, into a canonical mono
AAC rendition (.m4a). One format everywhere solves three problems at once:

- Gemini's documented audio inputs don't include webm — which is exactly what
  MediaRecorder produces on Chrome/Android (Phase 2 transcription reads the m4a).
- webm/opus recorded on Android doesn't reliably play back on iOS Safari; the
  m4a plays and seeks correctly on both sides of the debate.
- ffprobe on the m4a yields an authoritative duration, used for the server-side
  turn-length cap and (Phase 2) for validating transcript timestamps.

The original upload is stored untouched alongside; the m4a is a derived artifact.
ffmpeg is guaranteed in the Cloud Run image (Dockerfile). Local dev without it
degrades gracefully: no m4a, no duration — the flow still works off the original.

MediaRecorder blobs often carry no duration header (webm) or a trailing moov atom
(mp4), so both input and output go through real temp files, never pipes: mp4
demuxing needs seekable input, and +faststart needs a seekable output.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from .storage import ext_for

TRANSCODE_TIMEOUT_S = 60

# Mono keeps Gemini input small; 48 kHz / 64 kbps AAC is transparent for speech.
# +faststart moves the moov atom up front so browsers can seek immediately.
_FFMPEG_ARGS = [
    "-vn", "-ac", "1", "-ar", "48000", "-c:a", "aac", "-b:a", "64k",
    "-movflags", "+faststart",
]


class TranscodeError(Exception):
    """ffmpeg/ffprobe could not read or convert the uploaded audio."""


@lru_cache(maxsize=1)
def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _run(cmd: list, timeout: int = TRANSCODE_TIMEOUT_S) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd, capture_output=True, timeout=timeout, check=True, text=False
        )
    except subprocess.TimeoutExpired as e:
        raise TranscodeError(f"{cmd[0]} timed out") from e
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or b"").decode("utf-8", "replace").strip()[-500:]
        raise TranscodeError(f"{cmd[0]} failed: {detail}") from e


def probe_duration_s(path: Path) -> float:
    out = _run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ])
    try:
        return float(out.stdout.decode("ascii", "replace").strip())
    except ValueError as e:
        raise TranscodeError("ffprobe returned no duration") from e


import re

_VOL_RE = re.compile(r"(mean|max)_volume:\s*(-?[\d.]+)\s*dB")
_SIL_RE = re.compile(r"silence_(start|end):\s*(-?[\d.]+)")


# silencedetect gates: intervals ≥ SILENCE_MIN_S feed the audio-proof boundary
# snapping (a sentence pause is ~0.3–0.8s); speech_end_s keeps its original
# semantics by considering only intervals ≥ SPEECH_END_MIN_SILENCE_S (the
# transcription coverage check was calibrated against that — do not tighten it).
SILENCE_MIN_S = 0.3
SPEECH_END_MIN_SILENCE_S = 1.5


def analyze_audio(path: Path, duration_s: float) -> dict:
    """One ffmpeg pass -> {max_db, mean_db, speech_end_s, silences}.

    max_db is the speech gate's input: a dead mic capture measures around
    -91 dB while even whispered speech peaks far above -50 dB (forensics from
    real debates: silent turns -91, quietest real turn -6). speech_end_s is
    where audible content actually stops — the transcription coverage check
    compares the transcript's end against it, so a model that stops
    transcribing mid-speech gets caught. silences is the list of [start, end]
    quiet intervals (≥ SILENCE_MIN_S): the measured word boundaries that
    audio-proof anchors snap to (model timestamps are whole-second MM:SS).
    """
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path),
         "-af", f"volumedetect,silencedetect=n=-40dB:d={SILENCE_MIN_S}",
         "-f", "null", "-"],
        capture_output=True, timeout=TRANSCODE_TIMEOUT_S, text=True,
    )
    out = proc.stderr or ""
    vols = {m.group(1): float(m.group(2)) for m in _VOL_RE.finditer(out)}
    events = [(m.group(1), float(m.group(2))) for m in _SIL_RE.finditer(out)]
    # Pair events into silence intervals (silencedetect emits a closing
    # silence_end at EOF, so a trailing silence is a normal closed interval).
    silences, open_start = [], None
    for kind, ts in events:
        if kind == "start":
            open_start = max(0.0, ts)
        elif open_start is not None:
            silences.append((open_start, ts))
            open_start = None
    if open_start is not None:
        silences.append((open_start, duration_s))
    # Speech ends where a final LONG silence reaching EOF begins. Filtering by
    # length reproduces the original d=1.5 detector exactly (silencedetect
    # reports maximal quiet runs; d only filters them by duration).
    speech_end = duration_s
    long_silences = [iv for iv in silences
                     if iv[1] - iv[0] >= SPEECH_END_MIN_SILENCE_S]
    if long_silences and long_silences[-1][1] >= duration_s - 0.5:
        speech_end = long_silences[-1][0]
    return {
        "max_db": vols.get("max", 0.0),
        "mean_db": vols.get("mean", 0.0),
        "speech_end_s": round(max(0.0, min(speech_end, duration_s)), 2),
        "silences": [[round(s, 2), round(e, 2)] for s, e in silences],
    }


def transcode_to_m4a(data: bytes, content_type: str,
                     max_duration_s: "Optional[float]" = None
                     ) -> "tuple[bytes, float, dict]":
    """Original upload bytes -> (canonical m4a bytes, duration s, audio stats).

    `max_duration_s` trims the canonical rendition (ffmpeg -t): a recorder whose
    auto-stop fired late (throttled tab, suspended phone) must not lose the whole
    take — the first max_duration_s seconds ARE the legitimate speaking window,
    so we keep them and drop only the overrun. The stored original stays untouched.

    Raises TranscodeError if the input is unreadable — callers treat that as a
    bad upload, not a server error.
    """
    with tempfile.TemporaryDirectory(prefix="hakam-audio-") as tmp:
        src = Path(tmp) / f"in.{ext_for(content_type)}"
        dst = Path(tmp) / "out.m4a"
        src.write_bytes(data)
        trim = ["-t", str(float(max_duration_s))] if max_duration_s else []
        _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
              "-i", str(src), *_FFMPEG_ARGS, *trim, str(dst)])
        m4a = dst.read_bytes()
        if not m4a:
            raise TranscodeError("ffmpeg produced empty output")
        # Duration measured on the artifact clients actually seek in.
        duration = probe_duration_s(dst)
        return m4a, duration, analyze_audio(dst, duration)
