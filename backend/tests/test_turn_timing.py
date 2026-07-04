"""Submit-grace and forfeit timing.

A debater who speaks to the buzzer physically cannot START uploading until the
deadline has passed: the submit grace must accept that upload, and the no-show
forfeit for a STARTED turn must fire strictly after the submit window closes —
otherwise either side's 2s poll forfeits a turn whose upload is in flight
(the «لم تُسجَّل» bug).
"""
from datetime import timedelta

import pytest

from backend import config
from backend import state as S
from backend.audio import ffmpeg_available
from backend.store import get_store

from .conftest import make_tone
from .test_turn_flow import _json, _room_in_debate, _submit

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")


def _start_and_backdate(client, code, token, seconds_past_deadline):
    """Tap the mic, then rewind the speaking deadline into the past."""
    res = client.post(f"/api/rooms/{code}/turns/start",
                      headers={"X-Debater-Token": token})
    assert res.status_code == 200

    def backdate(r):
        r["turn_deadline_at"] = S.now_utc() - timedelta(seconds=seconds_past_deadline)

    get_store().update(code, backdate)


def test_buzzer_beater_upload_accepted_within_grace(client):
    # Upload lands well after the deadline but inside SUBMIT_GRACE: accepted.
    code, token_a, _ = _room_in_debate(client)
    _start_and_backdate(client, code, token_a, config.SUBMIT_GRACE_SECONDS - 15)
    view = _json(_submit(client, code, token_a, make_tone(3.0, "webm"),
                         "audio/webm", "turn.webm"))
    assert view["state"] == "turn_b1"
    assert view["turns"][0]["forfeited"] is False


def test_upload_past_submit_grace_rejected(client):
    # Past the grace the submit is rejected — but the turn is NOT yet forfeited
    # (rejection and forfeit are distinct clocks; forfeit comes NOSHOW later).
    code, token_a, _ = _room_in_debate(client)
    _start_and_backdate(client, code, token_a, config.SUBMIT_GRACE_SECONDS + 5)
    res = _submit(client, code, token_a, make_tone(3.0, "webm"),
                  "audio/webm", "turn.webm")
    assert res.status_code == 409
    assert res.get_json()["error"] == "turn_expired"
    assert _json(client.get(f"/api/rooms/{code}"))["state"] == "turn_a1"


def test_started_turn_forfeits_only_after_submit_window(client):
    code, token_a, _ = _room_in_debate(client)

    # Inside deadline + SUBMIT_GRACE + NOSHOW_GRACE: reconcile must NOT forfeit —
    # a legitimate full-length upload may still be in flight.
    _start_and_backdate(client, code, token_a, config.SUBMIT_GRACE_SECONDS + 5)
    view = _json(client.get(f"/api/rooms/{code}"))
    assert view["state"] == "turn_a1"

    # Beyond the full window the no-show forfeit applies.
    past = config.SUBMIT_GRACE_SECONDS + config.NOSHOW_GRACE_SECONDS + 5
    get_store().update(code, lambda r: r.__setitem__(
        "turn_deadline_at", S.now_utc() - timedelta(seconds=past)))
    view = _json(client.get(f"/api/rooms/{code}"))
    assert view["state"] == "turn_b1"
    assert view["turns"][0]["forfeited"] is True
