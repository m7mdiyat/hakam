"""«شارك الحكم» share links: sanitized 7-day public snapshots of a judged
debate, with audio copied under the bucket's shared/ prefix."""
import copy
from datetime import timedelta

import pytest

from backend import state as S
from backend.audio import ffmpeg_available
from backend.store import get_store

from .conftest import make_tone
from .test_turn_flow import _json, _room_in_debate, _submit

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")


def _room_with_verdict(client):
    code, tok_a, tok_b = _room_in_debate(client)
    # One real recorded turn so the share's audio-copy path is exercised.
    _json(_submit(client, code, tok_a, make_tone(3.0, "webm"),
                  "audio/webm", "turn.webm"))

    def done(r):
        r["state"] = S.DELIBERATING
        r["turn_deadline_at"] = r["turn_prep_deadline_at"] = None
        r["verdict"] = {"schema_version": 2, "winner": "a",
                        "score": {"a": 80, "b": 60}}

    get_store().update(code, done)
    return code, tok_a, tok_b


def _share(client, code, token):
    return client.post(f"/api/rooms/{code}/share",
                       headers={"X-Debater-Token": token})


def test_share_requires_verdict_and_a_debater(client):
    code, tok_a, _ = _room_in_debate(client)
    assert _share(client, code, tok_a).status_code == 409   # no verdict yet

    code, tok_a, _ = _room_with_verdict(client)
    assert _share(client, code, "wrong-token").status_code == 401


def test_share_is_idempotent_and_either_side_may_share(client):
    code, tok_a, tok_b = _room_with_verdict(client)
    sid = _json(_share(client, code, tok_a))["share_id"]
    assert len(sid) == 16
    assert _json(_share(client, code, tok_a))["share_id"] == sid
    assert _json(_share(client, code, tok_b))["share_id"] == sid


def _walk_for_secrets(obj, path=""):
    leaks = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if "token" in str(k).lower() or "secret" in str(k).lower():
                leaks.append(f"{path}.{k}")
            leaks += _walk_for_secrets(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            leaks += _walk_for_secrets(v, f"{path}[{i}]")
    return leaks


def test_shared_view_is_public_and_sanitized(client):
    code, tok_a, _ = _room_with_verdict(client)
    sid = _json(_share(client, code, tok_a))["share_id"]

    res = client.get(f"/api/shared/{sid}")          # no token at all
    assert res.status_code == 200
    view = _json(res)
    assert view["verdict"]["winner"] == "a"
    assert view["debaters"]["a"]["name"]
    assert _walk_for_secrets(view) == []
    # The stored snapshot itself must be secret-free too (it is world-served).
    doc = get_store().get_doc("shared", sid)
    assert _walk_for_secrets(doc) == []
    # audio uris stay server-side; the view carries only a playability flag
    assert all(set(t) >= {"turn", "audio"} and "audio_uri" not in t
               for t in view["turns"])


def test_shared_audio_streams_the_copied_blob(client):
    code, tok_a, _ = _room_with_verdict(client)
    sid = _json(_share(client, code, tok_a))["share_id"]
    view = _json(client.get(f"/api/shared/{sid}"))
    with_audio = [t for t in view["turns"] if t["audio"]]
    assert with_audio, "fixture submitted at least one turn with audio"
    res = client.get(f"/api/shared/{sid}/audio/{with_audio[0]['turn']}")
    assert res.status_code == 200
    assert res.mimetype == "audio/mp4"
    assert len(res.data) > 1000


def test_expired_or_unknown_share_says_session_ended(client):
    assert client.get("/api/shared/QQQQQQQQQQQQQQQQ").status_code == 404
    assert _json(client.get("/api/shared/QQQQQQQQQQQQQQQQ"))["error"] == "expired"

    code, tok_a, _ = _room_with_verdict(client)
    sid = _json(_share(client, code, tok_a))["share_id"]
    doc = get_store().get_doc("shared", sid)
    doc = copy.deepcopy(doc)
    doc["expires_at"] = S.now_utc() - timedelta(seconds=1)
    # rewrite the doc as expired (Firestore TTL deletion is lazy; reads gate)
    store = get_store()
    path = store._doc_path("shared", sid)
    store._write(path, doc)
    res = client.get(f"/api/shared/{sid}")
    assert res.status_code == 410
    assert _json(res)["error"] == "expired"
    assert client.get(f"/api/shared/{sid}/audio/turn_a1").status_code == 410
