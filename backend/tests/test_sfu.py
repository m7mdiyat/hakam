"""Broadcast SFU proxy (spectator live listening): debaters publish their mic
to Cloudflare, spectators listen from Cloudflare. The app secret never leaves
the server; session ids never enter the public view (only generations)."""
import pytest

from backend.store import get_store

from .test_turn_flow import _json, _room_in_debate

OFFER = {"type": "offer", "sdp": "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"}
ANSWER = {"type": "answer", "sdp": "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"}


@pytest.fixture
def fake_sfu(monkeypatch):
    """Record proxied Cloudflare calls; return canned session/track responses."""
    calls = []

    def _fake(path, payload=None, method="POST"):
        calls.append({"path": path, "payload": payload, "method": method})
        if path == "/sessions/new":
            return {"sessionId": f"sess{len(calls)}"}
        if path.endswith("/tracks/new"):
            return {"sessionDescription": {"type": "answer" if payload and
                    payload.get("sessionDescription") else "offer",
                    "sdp": "v=0\r\n..."},
                    "tracks": (payload or {}).get("tracks", [])}
        return {}

    monkeypatch.setattr("backend.rooms._sfu_call", _fake)
    return calls


def _spectate(client, code):
    return _json(client.post(f"/api/rooms/{code}/spectate",
                             json={"name": "مشاهد"}))["token"]


def test_publish_requires_debater_and_offer(client, fake_sfu):
    code, tok_a, _ = _room_in_debate(client)
    res = client.post(f"/api/rooms/{code}/sfu/publish", json={"sdp": OFFER, "mid": "0"},
                      headers={"X-Debater-Token": "wrong"})
    assert res.status_code == 401
    res = client.post(f"/api/rooms/{code}/sfu/publish", json={"sdp": ANSWER, "mid": "0"},
                      headers={"X-Debater-Token": tok_a})
    assert res.status_code == 400
    spec = _spectate(client, code)
    res = client.post(f"/api/rooms/{code}/sfu/publish", json={"sdp": OFFER, "mid": "0"},
                      headers={"X-Debater-Token": spec})
    assert res.status_code == 401  # spectators never publish


def test_publish_stores_generation_and_hides_session_id(client, fake_sfu):
    code, tok_a, _ = _room_in_debate(client)
    res = _json(client.post(f"/api/rooms/{code}/sfu/publish",
                            json={"sdp": OFFER, "mid": "0"},
                            headers={"X-Debater-Token": tok_a}))
    assert res["sdp"]["type"] == "answer"
    view = _json(client.get(f"/api/rooms/{code}"))
    assert view["sfu_published"] == {"a": 1, "b": 0}
    assert "sess" not in str(view)          # session ids never leave the doc
    # re-publish (page refresh) = new session, bumped generation
    client.post(f"/api/rooms/{code}/sfu/publish", json={"sdp": OFFER, "mid": "0"},
                headers={"X-Debater-Token": tok_a})
    view = _json(client.get(f"/api/rooms/{code}"))
    assert view["sfu_published"]["a"] == 2
    # the registered track is named after the side
    tracks_call = next(c for c in fake_sfu if c["path"].endswith("/tracks/new"))
    assert tracks_call["payload"]["tracks"][0]["trackName"] == "mic-a"


def test_listen_pulls_published_tracks_for_spectators(client, fake_sfu):
    code, tok_a, tok_b = _room_in_debate(client)
    spec = _spectate(client, code)
    # nothing published yet
    res = client.post(f"/api/rooms/{code}/sfu/listen", json={},
                      headers={"X-Debater-Token": spec})
    assert res.status_code == 409

    for tok in (tok_a, tok_b):
        client.post(f"/api/rooms/{code}/sfu/publish", json={"sdp": OFFER, "mid": "0"},
                    headers={"X-Debater-Token": tok})
    out = _json(client.post(f"/api/rooms/{code}/sfu/listen", json={},
                            headers={"X-Debater-Token": spec}))
    assert out["session_id"] and out["sdp"]["type"] == "offer"
    pull = fake_sfu[-1]
    names = {t["trackName"] for t in pull["payload"]["tracks"]}
    assert names == {"mic-a", "mic-b"}
    assert all(t["location"] == "remote" for t in pull["payload"]["tracks"])

    res = client.post(f"/api/rooms/{code}/sfu/renegotiate",
                      json={"session_id": out["session_id"], "sdp": ANSWER},
                      headers={"X-Debater-Token": spec})
    assert res.status_code == 200
    assert fake_sfu[-1]["method"] == "PUT"


def test_ice_now_serves_spectators_too(client, fake_sfu):
    code, _, _ = _room_in_debate(client)
    spec = _spectate(client, code)
    res = client.get(f"/api/rooms/{code}/ice", headers={"X-Debater-Token": spec})
    assert res.status_code == 200
    assert _json(res)["iceServers"]
    res = client.get(f"/api/rooms/{code}/ice", headers={"X-Debater-Token": "nope"})
    assert res.status_code == 401
