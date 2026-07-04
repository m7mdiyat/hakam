"""Round-count selector + turn gating (press-to-start clock, prep window)."""
from datetime import timedelta

import pytest

from backend import state as S
from backend.audio import ffmpeg_available
from backend.store import get_store

from .conftest import make_tone
from .test_turn_flow import _json, _room_in_debate, _submit

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")


def _setup_lobby(client):
    created = _json(client.post("/api/rooms", json={"topic": "موضوع"}))
    code, tok_a = created["code"], created["token"]
    tok_b = _json(client.post(f"/api/rooms/{code}/join", json={
        "name": "سارة", "claim": "دعوى ب", "consent": True}))["token"]
    return code, tok_a, tok_b


# --- format selector ---------------------------------------------------------
def test_creator_sets_rounds_and_ready_resets(client):
    code, tok_a, tok_b = _setup_lobby(client)
    client.post(f"/api/rooms/{code}/claim", json={"name": "أحمد", "claim": "دعوى أ"},
                headers={"X-Debater-Token": tok_a})
    client.post(f"/api/rooms/{code}/ready", json={"ready": True},
                headers={"X-Debater-Token": tok_b})

    view = _json(client.post(f"/api/rooms/{code}/format", json={"rounds_per_side": 3},
                             headers={"X-Debater-Token": tok_a}))
    assert view["format"]["rounds_per_side"] == 3
    assert len(view["turn_order"]) == 6
    # Format change un-readies everyone (re-consent).
    assert not view["debaters"]["a"]["ready"] and not view["debaters"]["b"]["ready"]


def test_format_forbidden_for_b_and_invalid_values(client):
    code, tok_a, tok_b = _setup_lobby(client)
    res = client.post(f"/api/rooms/{code}/format", json={"rounds_per_side": 3},
                      headers={"X-Debater-Token": tok_b})
    assert res.status_code == 403
    res = client.post(f"/api/rooms/{code}/format", json={"rounds_per_side": 7},
                      headers={"X-Debater-Token": tok_a})
    assert res.status_code == 400


def test_format_locked_once_debate_starts(client):
    code, tok_a, _ = _room_in_debate(client)
    res = client.post(f"/api/rooms/{code}/format", json={"rounds_per_side": 1},
                      headers={"X-Debater-Token": tok_a})
    assert res.status_code == 409


def test_creator_edits_topic_and_ready_resets(client):
    code, tok_a, tok_b = _setup_lobby(client)
    client.post(f"/api/rooms/{code}/ready", json={"ready": True},
                headers={"X-Debater-Token": tok_b})
    view = _json(client.post(f"/api/rooms/{code}/topic",
                             json={"topic": "موضوع جديد تمامًا"},
                             headers={"X-Debater-Token": tok_a}))
    assert view["topic"] == "موضوع جديد تمامًا"
    # Topic change un-readies everyone (re-consent, like /format).
    assert not view["debaters"]["a"]["ready"] and not view["debaters"]["b"]["ready"]


def test_topic_forbidden_for_b_and_empty_rejected(client):
    code, tok_a, tok_b = _setup_lobby(client)
    res = client.post(f"/api/rooms/{code}/topic", json={"topic": "آخر"},
                      headers={"X-Debater-Token": tok_b})
    assert res.status_code == 403
    res = client.post(f"/api/rooms/{code}/topic", json={"topic": "   "},
                      headers={"X-Debater-Token": tok_a})
    assert res.status_code == 400


def test_topic_locked_once_debate_starts(client):
    code, tok_a, _ = _room_in_debate(client)
    res = client.post(f"/api/rooms/{code}/topic", json={"topic": "آخر"},
                      headers={"X-Debater-Token": tok_a})
    assert res.status_code == 409


def test_claim_reedit_updates_and_unreadies(client):
    # The lobby edit affordance leans on this: re-editing a claim pre-debate
    # updates it and drops the editor's ready flag («re-confirm after editing»).
    code, _, tok_b = _setup_lobby(client)
    client.post(f"/api/rooms/{code}/ready", json={"ready": True},
                headers={"X-Debater-Token": tok_b})
    view = _json(client.post(f"/api/rooms/{code}/claim",
                             json={"name": "سارة", "claim": "دعوى معدّلة"},
                             headers={"X-Debater-Token": tok_b}))
    assert view["debaters"]["b"]["claim"] == "دعوى معدّلة"
    assert view["debaters"]["b"]["ready"] is False


def test_one_round_debate_reaches_deliberating(client):
    code, tok_a, tok_b = _setup_lobby(client)
    client.post(f"/api/rooms/{code}/format", json={"rounds_per_side": 1},
                headers={"X-Debater-Token": tok_a})
    client.post(f"/api/rooms/{code}/claim", json={"name": "أحمد", "claim": "دعوى أ"},
                headers={"X-Debater-Token": tok_a})
    for tok in (tok_a, tok_b):
        client.post(f"/api/rooms/{code}/ready", json={"ready": True},
                    headers={"X-Debater-Token": tok})
    tone = make_tone(3.0, "webm")
    view = _json(_submit(client, code, tok_a, tone, "audio/webm", "t.webm"))
    assert view["state"] == "turn_b1"
    view = _json(_submit(client, code, tok_b, tone, "audio/webm", "t.webm"))
    assert view["state"] == "deliberating"


# --- turn gating -------------------------------------------------------------
def test_clock_starts_on_mic_press_not_on_turn_change(client):
    code, tok_a, tok_b = _room_in_debate(client)
    view = _json(client.get(f"/api/rooms/{code}"))
    assert view["state"] == "turn_a1"
    assert view["turn_started"] is False
    assert view["turn_deadline_at"] is None
    assert view["turn_prep_deadline_at"] is not None

    # Only the turn holder can start the clock.
    res = client.post(f"/api/rooms/{code}/turns/start", headers={"X-Debater-Token": tok_b})
    assert res.status_code == 409

    view = _json(client.post(f"/api/rooms/{code}/turns/start",
                             headers={"X-Debater-Token": tok_a}))
    assert view["turn_started"] is True
    assert view["turn_deadline_at"] is not None
    assert view["turn_prep_deadline_at"] is None

    # Idempotent: a second press keeps the original deadline.
    again = _json(client.post(f"/api/rooms/{code}/turns/start",
                              headers={"X-Debater-Token": tok_a}))
    assert again["turn_deadline_at"] == view["turn_deadline_at"]

    # Upload flow continues to work; next turn is back in prep state.
    view = _json(_submit(client, code, tok_a, make_tone(3.0, "webm"),
                         "audio/webm", "t.webm"))
    assert view["state"] == "turn_b1"
    assert view["turn_started"] is False and view["turn_prep_deadline_at"] is not None


def test_expired_prep_window_forfeits_turn(client):
    code, _, _ = _room_in_debate(client)

    def expire(r):
        r["turn_prep_deadline_at"] = S.now_utc() - timedelta(seconds=60)

    get_store().update(code, expire)
    view = _json(client.get(f"/api/rooms/{code}"))  # reconcile applies the forfeit
    assert view["state"] == "turn_b1"
    assert view["turns"][0]["forfeited"] is True
