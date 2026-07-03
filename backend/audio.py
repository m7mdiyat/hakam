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


def transcode_to_m4a(data: bytes, content_type: str) -> "tuple[bytes, float]":
    """Original upload bytes -> (canonical m4a bytes, duration in seconds).

    Raises TranscodeError if the input is unreadable — callers treat that as a
    bad upload, not a server error.
    """
    with tempfile.TemporaryDirectory(prefix="hakam-audio-") as tmp:
        src = Path(tmp) / f"in.{ext_for(content_type)}"
        dst = Path(tmp) / "out.m4a"
        src.write_bytes(data)
        _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
              "-i", str(src), *_FFMPEG_ARGS, str(dst)])
        m4a = dst.read_bytes()
        if not m4a:
            raise TranscodeError("ffmpeg produced empty output")
        # Duration measured on the artifact clients actually seek in.
        return m4a, probe_duration_s(dst)
