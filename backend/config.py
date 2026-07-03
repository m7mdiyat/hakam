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

# --- Gemini via Vertex AI (Phase 2) ------------------------------------------
# Auth = Application Default Credentials, exactly like Firestore/GCS: the Cloud
# Run runtime service account in production (needs roles/aiplatform.user), your
# gcloud ADC locally. No API key anywhere — nothing to leak, rotate, or store.
GEMINI_MODEL = os.environ.get("HAKAM_GEMINI_MODEL", "gemini-3.5-flash")
GEMINI_TIMEOUT_S = _int("HAKAM_GEMINI_TIMEOUT_S", 60)
# "global" routes to wherever the model has capacity (Gemini isn't hosted in
# me-central1); pin a specific region via env if data residency ever demands it.
VERTEX_LOCATION = os.environ.get("HAKAM_VERTEX_LOCATION", "global")
# Gate for all model calls: on by default in cloud mode, opt-in locally (local
# calls need ADC — see .env.example). Off => transcript stays null, Phase-1
# flows and tests run untouched.
GEMINI_ENABLED = os.environ.get(
    "HAKAM_GEMINI_ENABLED", "" if LOCAL_MODE else "1"
).strip().lower() in ("1", "true", "yes")
TRANSCRIBE_ENABLED = GEMINI_ENABLED

# --- Judge ensemble -----------------------------------------------------------
# One judging run may be claimed at a time; a crashed run's lease expires after
# this many seconds so a client retrigger (POST /judge) can reclaim it.
JUDGE_LEASE_SECONDS = _int("HAKAM_JUDGE_LEASE_SECONDS", 90)
# Thinking budgets (tokens): probes reason hard, synthesis narrates.
JUDGE_THINKING_BUDGET = _int("HAKAM_JUDGE_THINKING_BUDGET", 2048)
SYNTH_THINKING_BUDGET = _int("HAKAM_SYNTH_THINKING_BUDGET", 1024)

# --- Transcription queue (Cloud Tasks) ---------------------------------------
# Turn uploads enqueue transcription instead of blocking the uploader; the queue
# POSTs back to /api/internal/transcribe with an OIDC token minted for TASKS_SA,
# which the endpoint verifies (the service itself is public). LOCAL_MODE uses a
# plain background thread instead — no queue, no GCP.
TASKS_QUEUE = os.environ.get("HAKAM_TASKS_QUEUE", "hakam-transcribe")
TASKS_SA_EMAIL = os.environ.get("HAKAM_TASKS_SA_EMAIL", "")
# This service's own public URL — the queue's target and the OIDC audience.
SELF_URL = os.environ.get(
    "HAKAM_SELF_URL", "https://hakam-176728126674.me-central1.run.app"
).rstrip("/")

# --- Local dev paths --------------------------------------------------------
_ROOT = Path(__file__).resolve().parent.parent
LOCAL_STORE_DIR = Path(os.environ.get("HAKAM_LOCAL_STORE_DIR", _ROOT / ".localstore"))
LOCAL_AUDIO_DIR = Path(os.environ.get("HAKAM_LOCAL_AUDIO_DIR", _ROOT / ".localaudio"))

# The frontend lives on GitHub Pages; the Cloud Run root redirects here.
FRONTEND_URL = os.environ.get("HAKAM_FRONTEND_URL", "https://thehakam.com").rstrip("/")
