"""Central configuration, read once from the environment.

Production (Cloud Run) leaves HAKAM_LOCAL unset -> Firestore + GCS.
Local dev sets HAKAM_LOCAL=1 -> file-backed store + local-disk audio, no GCP needed.
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    # Load repo-root .env if present (local dev). Safe no-op in production.
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:  # dotenv is optional at runtime
    pass


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# --- Mode -------------------------------------------------------------------
LOCAL_MODE = os.environ.get("HAKAM_LOCAL", "").strip() in ("1", "true", "yes")

# --- Google Cloud -----------------------------------------------------------
PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "hakam-501212")
REGION = os.environ.get("HAKAM_REGION", "me-central1")
AUDIO_BUCKET = os.environ.get("HAKAM_AUDIO_BUCKET", "hakam-audio")
FIRESTORE_DATABASE = os.environ.get("HAKAM_FIRESTORE_DB", "(default)")

# --- CORS (the GitHub Pages frontend calls this API cross-origin) ---
# Comma-separated allow-list; NOT a wildcard. Default: production apex + local dev.
CORS_ORIGINS = os.environ.get(
    "HAKAM_CORS_ORIGINS", "https://thehakam.com,http://localhost:5173"
).split(",")

# --- Debate format (server-authoritative) -----------------------------------
TURN_SECONDS = _int("HAKAM_TURN_SECONDS", 120)
ROUNDS_PER_SIDE = _int("HAKAM_ROUNDS_PER_SIDE", 2)

# --- Timer / lifecycle grace values -----------------------------------------
# Hard server cap: a turn upload is rejected after deadline + this many seconds.
SUBMIT_GRACE_SECONDS = _int("HAKAM_SUBMIT_GRACE_SECONDS", 3)
# A turn with NO recording auto-forfeits (advances) at deadline + this many seconds.
NOSHOW_GRACE_SECONDS = _int("HAKAM_NOSHOW_GRACE_SECONDS", 10)
# Non-terminal room with no activity for this long -> abandoned.
ABANDON_MINUTES = _int("HAKAM_ABANDON_MINUTES", 30)
# Room hard-expires (410) this many hours after creation; also drives Firestore TTL.
ROOM_TTL_HOURS = _int("HAKAM_ROOM_TTL_HOURS", 24)

# --- Upload limits ----------------------------------------------------------
# Independent server-side byte cap (UI soft-stop is not trusted). ~2 min Opus is
# well under a few MB; we allow generous headroom but keep it bounded.
MAX_AUDIO_BYTES = _int("HAKAM_MAX_AUDIO_BYTES", 12 * 1024 * 1024)
# Real-duration cap, measured by ffprobe at upload: reject audio longer than
# TURN_SECONDS + this grace. Keeping turns short is also what keeps Phase-2
# transcript timestamps accurate (drift grows with clip length).
AUDIO_DURATION_GRACE_SECONDS = _int("HAKAM_AUDIO_DURATION_GRACE_SECONDS", 10)
ALLOWED_AUDIO_MIMES = ("audio/webm", "audio/mp4", "audio/ogg", "audio/mpeg", "audio/aac")

# --- Rate limiting ----------------------------------------------------------
# Room creation attempts allowed per IP per rolling window.
CREATE_RATE_LIMIT = _int("HAKAM_CREATE_RATE_LIMIT", 6)
CREATE_RATE_WINDOW_SECONDS = _int("HAKAM_CREATE_RATE_WINDOW_SECONDS", 60)

# --- Local dev paths --------------------------------------------------------
_ROOT = Path(__file__).resolve().parent.parent
LOCAL_STORE_DIR = Path(os.environ.get("HAKAM_LOCAL_STORE_DIR", _ROOT / ".localstore"))
LOCAL_AUDIO_DIR = Path(os.environ.get("HAKAM_LOCAL_AUDIO_DIR", _ROOT / ".localaudio"))

# The frontend lives on GitHub Pages; the Cloud Run root redirects here.
FRONTEND_URL = os.environ.get("HAKAM_FRONTEND_URL", "https://thehakam.com").rstrip("/")
