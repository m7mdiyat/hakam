"""Background transcription dispatch.

Production: a Cloud Tasks queue POSTs back to /api/internal/transcribe with an
OIDC token minted for TASKS_SA_EMAIL; the (public) service verifies audience +
service-account email in-app, so the queue needs no invoker IAM on the service.
Cloud Tasks also owns retries — the endpoint returns 5xx on failure and the
queue backs off and retries up to its max-attempts.

Local dev (HAKAM_LOCAL=1): a daemon thread calls transcribe_turn() directly —
same code path as the worker endpoint, minus HTTP.

Enqueueing is fire-and-forget by design: a lost task must never fail the turn
upload. The judge's preflight (Phase-2c) re-runs any still-missing transcripts,
so the worst case for a dropped task is a late transcript, not a lost one.
"""
from __future__ import annotations

import json
import logging
import threading

from . import config

log = logging.getLogger("hakam.tasks")

_tasks_client = None


def _get_tasks_client():
    global _tasks_client
    if _tasks_client is None:
        from google.cloud import tasks_v2
        _tasks_client = tasks_v2.CloudTasksClient()
    return _tasks_client


def _enqueue_cloud_task(code: str, turn_key: str) -> None:
    from google.cloud import tasks_v2

    client = _get_tasks_client()
    parent = client.queue_path(config.PROJECT_ID, config.REGION, config.TASKS_QUEUE)
    task = tasks_v2.Task(
        http_request=tasks_v2.HttpRequest(
            http_method=tasks_v2.HttpMethod.POST,
            url=f"{config.SELF_URL}/api/internal/transcribe",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"code": code, "turn": turn_key}).encode(),
            oidc_token=tasks_v2.OidcToken(
                service_account_email=config.TASKS_SA_EMAIL,
                audience=config.SELF_URL,
            ),
        )
    )
    client.create_task(parent=parent, task=task)


def _run_in_thread(code: str, turn_key: str) -> None:
    from .transcribe import transcribe_turn

    def work():
        try:
            transcribe_turn(code, turn_key)
        except Exception:
            log.exception("local transcription failed for %s/%s", code, turn_key)

    threading.Thread(target=work, daemon=True, name=f"transcribe-{turn_key}").start()


def enqueue_transcription(code: str, turn_key: str) -> None:
    """Schedule transcription for a just-uploaded turn. Never raises."""
    if not config.TRANSCRIBE_ENABLED:
        return
    try:
        if config.LOCAL_MODE or not config.TASKS_SA_EMAIL:
            _run_in_thread(code, turn_key)
        else:
            _enqueue_cloud_task(code, turn_key)
    except Exception:
        log.exception("failed to enqueue transcription for %s/%s", code, turn_key)


def verify_task_oidc(authorization_header: str) -> bool:
    """Validate the Cloud Tasks OIDC bearer token on the internal endpoint."""
    if not config.TASKS_SA_EMAIL:
        return False  # endpoint disabled unless explicitly configured
    if not authorization_header.startswith("Bearer "):
        return False
    token = authorization_header[len("Bearer "):].strip()
    try:
        from google.auth.transport import requests as garequests
        from google.oauth2 import id_token
        claims = id_token.verify_oauth2_token(
            token, garequests.Request(), audience=config.SELF_URL
        )
        return bool(claims.get("email") == config.TASKS_SA_EMAIL
                    and claims.get("email_verified"))
    except Exception:
        return False
