"""Live-audio signaling: debater-only blobs over the poll, gen supersession,
size cap, ICE endpoint shape — and the privacy line (ICE candidates carry
device IPs, so spectators/anonymous pollers must never see rtc)."""
from datetime import timedelta

import pytest

from backend import config
from backend import state as S
from backend.audio import ffmpeg_available
from backend.store import get_store

from .test_turn_flow import _assert_firestore_safe, _json, _room_in_debate

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")

OFFER = {"type": "offer", "sdp": "v=0 fake-offer"}
ANSWER = {"type": "answer", "sdp": "v=0 fake-answer"}


def _post(client, code, token, payload):
    return client.post(f"/api/rooms/{code}/rtc", json=payload,
                       headers={"X-Debater-Token": token})


def test_signal_roundtrip_is_debater_only(client):
    code, tok_a, tok_b = _room_in_debate(client)
    assert _post(client, code, tok_a, {"gen": 1, "sdp": OFFER}).status_code == 200

    # B's poll carries A's offer.
    view = _json(client.get(f"/api/rooms/{code}", headers={"X-Debater-Token": tok_b}))
    assert view["rtc"]["a"] == {"gen": 1, "sdp": OFFER, "restart": False}
    assert view["rtc"]["b"] is None

    # Anonymous and spectator polls never see rtc (nor does public_view).
    assert "rtc" not in _json(client.get(f"/api/rooms/{code}"))
    spec = _json(client.post(f"/api/rooms/{code}/spectate", json={"name": "خالد"}))
    view = _json(client.get(f"/api/rooms/{code}",
                            headers={"X-Debater-Token": spec["token"]}))
    assert "rtc" not in view
    assert "rtc" not in S.public_view(get_store().get(code))
    _assert_firestore_safe(get_store().get(code))


def test_gen_supersession_and_restart_request(client):
    code, tok_a, tok_b = _room_in_debate(client)
    _post(client, code, tok_a, {"gen": 1, "sdp": OFFER})
    _post(client, code, tok_a, {"gen": 2, "sdp": OFFER})
    _post(client, code, tok_b, {"gen": 2, "restart": True})  # no sdp: a restart ask

    view = _json(client.get(f"/api/rooms/{code}", headers={"X-Debater-Token": tok_b}))
    assert view["rtc"]["a"]["gen"] == 2
    assert view["rtc"]["b"] == {"gen": 2, "sdp": None, "restart": True}


def test_stale_signal_collapses_to_gen_stub(client):
    code, tok_a, tok_b = _room_in_debate(client)
    _post(client, code, tok_a, {"gen": 3, "sdp": OFFER})

    def age(r):
        r["rtc"]["a"]["at"] = S.now_utc() - timedelta(
            seconds=config.RTC_SIGNAL_FRESH_SECONDS + 5)

    get_store().update(code, age)
    view = _json(client.get(f"/api/rooms/{code}", headers={"X-Debater-Token": tok_b}))
    assert view["rtc"]["a"] == {"gen": 3, "stale": True}


def test_signal_validation(client):
    code, tok_a, _ = _room_in_debate(client)
    assert _post(client, code, tok_a, {"sdp": OFFER}).status_code == 400       # no gen
    assert _post(client, code, tok_a, {"gen": 1}).status_code == 400           # no sdp/restart
    assert _post(client, code, tok_a,
                 {"gen": 1, "sdp": {"type": "banana", "sdp": "x"}}).status_code == 400
    big = {"type": "offer", "sdp": "x" * (config.RTC_MAX_SDP_BYTES + 1)}
    assert _post(client, code, tok_a, {"gen": 1, "sdp": big}).status_code == 413
    res = client.post(f"/api/rooms/{code}/rtc", json={"gen": 1, "sdp": OFFER})
    assert res.status_code == 401                                              # no token


def test_ice_endpoint_stun_only_without_turn_keys(client):
    # Tests never call Cloudflare: TURN keys are unset -> STUN-only shape.
    code, tok_a, _ = _room_in_debate(client)
    view = _json(client.get(f"/api/rooms/{code}/ice",
                            headers={"X-Debater-Token": tok_a}))
    assert view["iceServers"] == [{"urls": config.STUN_URLS}]
    res = client.get(f"/api/rooms/{code}/ice")
    assert res.status_code == 401
