"""Presence: token-carrying polls bump last_seen; stale debaters show offline."""
from datetime import timedelta

from backend import state as S
from backend.store import get_store

from .test_turn_flow import _json


def _lobby(client):
    created = _json(client.post("/api/rooms", json={"topic": "موضوع"}))
    code, tok_a = created["code"], created["token"]
    tok_b = _json(client.post(f"/api/rooms/{code}/join", json={
        "name": "سارة", "claim": "د", "consent": True}))["token"]
    return code, tok_a, tok_b


def test_polling_with_token_keeps_debater_online(client):
    code, tok_a, _ = _lobby(client)
    view = _json(client.get(f"/api/rooms/{code}", headers={"X-Debater-Token": tok_a}))
    assert view["debaters"]["a"]["online"] is True
    assert view["debaters"]["b"]["online"] is True   # join just set last_seen


def test_stale_debater_shows_offline_and_recovers(client):
    code, tok_a, _ = _lobby(client)

    def age(r):
        r["debaters"]["a"]["last_seen_at"] = S.now_utc() - timedelta(seconds=60)
    get_store().update(code, age)

    # An anonymous poll doesn't bump anyone: A reads offline.
    view = _json(client.get(f"/api/rooms/{code}"))
    assert view["debaters"]["a"]["online"] is False

    # A's own poll (token) bumps presence: back online.
    view = _json(client.get(f"/api/rooms/{code}", headers={"X-Debater-Token": tok_a}))
    assert view["debaters"]["a"]["online"] is True
