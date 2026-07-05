"""Spectator mode: named read-only viewers — join, cap, presence, audio access."""
import json
from datetime import timedelta

import pytest

from backend import config
from backend import state as S
from backend.audio import ffmpeg_available
from backend.store import get_store

from .conftest import make_tone
from .test_turn_flow import _json, _room_in_debate, _submit

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")


def _spectate(client, code, name="خالد"):
    return client.post(f"/api/rooms/{code}/spectate", json={"name": name})


def test_spectator_joins_and_appears_named_online(client):
    code, _, _ = _room_in_debate(client)  # mid-debate join is allowed
    res = _spectate(client, code)
    assert res.status_code == 201
    token = _json(res)["token"]

    view = _json(client.get(f"/api/rooms/{code}"))
    assert view["spectators"] == [{"name": "خالد", "online": True}]
    # The spectator token never appears anywhere in the public view.
    assert token not in json.dumps(view)


def test_spectator_name_required(client):
    code, _, _ = _room_in_debate(client)
    assert _spectate(client, code, name="  ").status_code == 400


def test_spectator_cap(client, monkeypatch):
    monkeypatch.setattr(config, "SPECTATOR_MAX", 2)
    code, _, _ = _room_in_debate(client)
    assert _spectate(client, code, "أ").status_code == 201
    assert _spectate(client, code, "ب").status_code == 201
    res = _spectate(client, code, "ج")
    assert res.status_code == 409
    assert res.get_json()["error"] == "spectators_full"


def test_spectator_presence_goes_offline_and_bumps_back(client):
    code, _, _ = _room_in_debate(client)
    token = _json(_spectate(client, code))["token"]

    def backdate(r):
        for s in r["spectators"].values():
            s["last_seen_at"] = S.now_utc() - timedelta(
                seconds=config.SPECTATOR_PRESENCE_TTL_SECONDS + 5)

    get_store().update(code, backdate)
    # Anonymous view: the stale spectator reads as offline.
    view = _json(client.get(f"/api/rooms/{code}"))
    assert view["spectators"][0]["online"] is False
    # The spectator's own poll bumps them back online.
    view = _json(client.get(f"/api/rooms/{code}",
                            headers={"X-Debater-Token": token}))
    assert view["spectators"][0]["online"] is True


def test_spectator_can_play_turn_audio_but_stranger_cannot(client):
    code, tok_a, _ = _room_in_debate(client)
    _json(_submit(client, code, tok_a, make_tone(3.0, "webm"),
                  "audio/webm", "t.webm"))
    token = _json(_spectate(client, code))["token"]

    res = client.get(f"/api/rooms/{code}/turns/turn_a1/audio",
                     headers={"X-Debater-Token": token})
    assert res.status_code == 200 and res.mimetype == "audio/mp4"
    res = client.get(f"/api/rooms/{code}/turns/turn_a1/audio",
                     headers={"X-Debater-Token": "not-a-token"})
    assert res.status_code == 401


def test_spectator_does_not_keep_room_alive(client):
    # Viewers must not stop an idle debate from abandoning.
    code, _, _ = _room_in_debate(client)
    _spectate(client, code)

    def idle(r):
        r["last_activity_at"] = S.now_utc() - timedelta(
            minutes=config.ABANDON_MINUTES + 1)

    get_store().update(code, idle)
    view = _json(client.get(f"/api/rooms/{code}"))
    assert view["state"] == "abandoned"
