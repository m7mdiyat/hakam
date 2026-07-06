"""Test bootstrap: force local mode into a throwaway dir BEFORE backend.config
is imported (config reads the environment once at import time)."""
import os
import subprocess
import tempfile
from functools import lru_cache

import pytest

_tmp = tempfile.mkdtemp(prefix="hakam-test-")
os.environ["HAKAM_LOCAL"] = "1"
os.environ["HAKAM_LOCAL_STORE_DIR"] = os.path.join(_tmp, "store")
os.environ["HAKAM_LOCAL_AUDIO_DIR"] = os.path.join(_tmp, "audio")
os.environ["HAKAM_CREATE_RATE_LIMIT"] = "1000"  # each test creates a room
os.environ["HAKAM_GEMINI_ENABLED"] = "0"  # model calls stay off unless a test patches it


@lru_cache(maxsize=None)
def make_tone(seconds: float, fmt: str) -> bytes:
    """Synthesize a sine-tone clip like a MediaRecorder upload would look.
    fmt: 'webm' (Chrome/Android: opus) or 'm4a' (iOS Safari: AAC in mp4)."""
    codec = {"webm": "libopus", "m4a": "aac"}[fmt]
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, f"tone.{fmt}")
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
             "-c:a", codec, out],
            check=True, capture_output=True, timeout=120,
        )
        with open(out, "rb") as f:
            return f.read()


@lru_cache(maxsize=None)
def make_gapped_tone(pre_s: float, gap_s: float, post_s: float,
                     tail_s: float = 0.0) -> bytes:
    """Tone with a silent gap (and optional silent tail): pre_s of tone, gap_s
    of silence, post_s of tone, tail_s of silence — for silence-interval and
    speech-end assertions."""
    total = pre_s + gap_s + post_s + tail_s
    mute = f"volume=0:enable='between(t,{pre_s},{pre_s + gap_s})'"
    if tail_s:
        mute += f",volume=0:enable='gte(t,{pre_s + gap_s + post_s})'"
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "gapped.webm")
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", f"sine=frequency=440:duration={total}",
             "-af", mute, "-c:a", "libopus", out],
            check=True, capture_output=True, timeout=120,
        )
        with open(out, "rb") as f:
            return f.read()


@lru_cache(maxsize=None)
def make_silence(seconds: float) -> bytes:
    """A dead-mic capture: digital silence in webm/opus (the EY52EC scenario)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "silence.webm")
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
             "-t", str(seconds), "-c:a", "libopus", out],
            check=True, capture_output=True, timeout=120,
        )
        with open(out, "rb") as f:
            return f.read()


@pytest.fixture(autouse=True)
def _sijal_off():
    """سجال is an optional post-debate round; keep it OFF for the existing
    flows (they expect turns -> deliberating directly). test_sijal opts in."""
    from backend import config
    prev = config.SIJAL_ENABLED
    config.SIJAL_ENABLED = False
    yield
    config.SIJAL_ENABLED = prev


@pytest.fixture(scope="session")
def client():
    from backend.app import app
    return app.test_client()
