"""Verdict-screen rematch: the creator restarts with the same opponent in a
linked fresh room; both seats, topic, format and tokens carry over."""
import pytest

from backend import state as S
from backend.audio import ffmpeg_available
from backend.store import get_store

from .test_turn_flow import _json, _room_in_debate

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")


def _room_with_verdict(client):
    code, tok_a, tok_b = _room_in_debate(client)

    def done(r):
        r["state"] = S.DELIBERATING
        r["turn_deadline_at"] = r["turn_prep_deadline_at"] = None
        r["verdict"] = {"schema_version": 2, "winner": "a"}

    get_store().update(code, done)
    return code, tok_a, tok_b


def test_rematch_requires_verdict_and_creator(client):
    code, tok_a, _ = _room_in_debate(client)
    res = client.post(f"/api/rooms/{code}/rematch",
                      headers={"X-Debater-Token": tok_a})
    assert res.status_code == 409          # no verdict yet

    code, tok_a, tok_b = _room_with_verdict(client)
    res = client.post(f"/api/rooms/{code}/rematch",
                      headers={"X-Debater-Token": tok_b})
    assert res.status_code == 403          # creator only


def test_rematch_creates_linked_room_with_both_seated(client):
    code, tok_a, tok_b = _room_with_verdict(client)
    new_code = _json(client.post(f"/api/rooms/{code}/rematch",
                                 headers={"X-Debater-Token": tok_a}))["code"]
    assert new_code != code

    # The old room now advertises the rematch to the opponent's poll.
    old = _json(client.get(f"/api/rooms/{code}"))
    assert old["rematch_code"] == new_code

    # New room: both seated with carried names/claims/format, nobody ready.
    view = _json(client.get(f"/api/rooms/{new_code}"))
    assert view["state"] == "claims"
    assert view["debaters"]["a"]["joined"] and view["debaters"]["b"]["joined"]
    assert view["debaters"]["a"]["claim"] == "الدعوى الأولى"
    assert view["debaters"]["b"]["claim"] == "الدعوى الثانية"
    assert not view["debaters"]["a"]["ready"]
    assert not view["debaters"]["b"]["ready"]
    assert view["turns"] == [] and view["verdict"] is None

    # The old tokens hold the new room's seats (the client-side redirect
    # reuses them — no secret delivery channel needed).
    res = client.post(f"/api/rooms/{new_code}/ready", json={"ready": True},
                      headers={"X-Debater-Token": tok_b})
    assert res.status_code == 200
    assert _json(res)["debaters"]["b"]["ready"] is True


def test_rematch_is_idempotent(client):
    code, tok_a, _ = _room_with_verdict(client)
    first = _json(client.post(f"/api/rooms/{code}/rematch",
                              headers={"X-Debater-Token": tok_a}))["code"]
    again = _json(client.post(f"/api/rooms/{code}/rematch",
                              headers={"X-Debater-Token": tok_a}))["code"]
    assert again == first
