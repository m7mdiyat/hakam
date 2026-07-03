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


@pytest.fixture(scope="session")
def client():
    from backend.app import app
    return app.test_client()
