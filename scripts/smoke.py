"""End-to-end backend smoke test (LOCAL mode, no GCP needed).

Run:  python scripts/smoke.py
Drives the full Phase-1 flow through the Flask test client and asserts every
state transition, guard, the audio proxy, rate limiting, and the lazy timers.
"""
import os
import io
import sys
import tempfile
import shutil
from datetime import timedelta

TMP = tempfile.mkdtemp(prefix="hakam-smoke-")
os.environ["HAKAM_LOCAL"] = "1"
os.environ["HAKAM_LOCAL_STORE_DIR"] = os.path.join(TMP, "store")
os.environ["HAKAM_LOCAL_AUDIO_DIR"] = os.path.join(TMP, "audio")
os.environ["HAKAM_TURN_SECONDS"] = "120"
os.environ["HAKAM_CREATE_RATE_LIMIT"] = "3"
os.environ.setdefault("PYTHONWARNINGS", "ignore")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from backend.app import app          # noqa: E402
from backend import state as S       # noqa: E402

c = app.test_client()
ok = 0


def check(cond, label):
    global ok
    assert cond, f"FAIL: {label}"
    ok += 1
    print(f"  ok  {label}")


def audio():
    return {
        "audio": (io.BytesIO(b"\x1a\x45\xdf\xa3fake-audio-bytes" * 20), "t.webm", "audio/webm"),
        "duration_ms": "5000",
    }


def mpost(path, tok):
    return c.post(path, data=audio(), headers={"X-Debater-Token": tok},
                  content_type="multipart/form-data")


print("health")
r = c.get("/healthz")
check(r.status_code == 200 and r.get_json()["mode"] == "local", "healthz local")

print("create room (A)")
r = c.post("/api/rooms", json={"topic": "هل التعليم الجامعي ضروري؟"})
check(r.status_code == 201, "create 201")
code, tokA = r.get_json()["code"], r.get_json()["token"]
check(len(code) == 6 and all(ch in "ABCDEFGHJKLMNPQRSTUVWXYZ23456789" for ch in code), "code format")

j = c.get(f"/api/rooms/{code}").get_json()
check(j["state"] == "lobby", "state lobby")
check(bool(j["server_now"]) and "secret_tokens" not in j, "server_now present, tokens not leaked")

print("A sets claim")
r = c.post(f"/api/rooms/{code}/claim", json={"name": "عبدالله", "claim": "الشهادة أضمن"},
           headers={"X-Debater-Token": tokA})
check(r.status_code == 200 and r.get_json()["debaters"]["a"]["name"] == "عبدالله", "A claim set")

print("join without consent -> 400")
r = c.post(f"/api/rooms/{code}/join", json={"name": "سلطان", "claim": "المهارة أهم"})
check(r.status_code == 400 and r.get_json()["error"] == "consent_required", "consent required")

print("join (B)")
r = c.post(f"/api/rooms/{code}/join", json={"name": "سلطان", "claim": "المهارة أهم", "consent": True})
check(r.status_code == 201, "join 201")
tokB = r.get_json()["token"]
check(r.get_json()["room"]["state"] == "claims", "state claims after join")

r = c.post(f"/api/rooms/{code}/join", json={"name": "X", "claim": "Y", "consent": True})
check(r.status_code == 409 and r.get_json()["error"] == "room_full", "room full")

print("ready -> start")
c.post(f"/api/rooms/{code}/ready", headers={"X-Debater-Token": tokA})
r = c.post(f"/api/rooms/{code}/ready", headers={"X-Debater-Token": tokB})
j = r.get_json()
check(j["state"] == "turn_a1" and j["turn_deadline_at"], "both ready -> turn_a1 with deadline")

print("guards")
check(mpost(f"/api/rooms/{code}/turns", tokB).status_code == 409, "B blocked on A's turn")
check(mpost(f"/api/rooms/{code}/turns", "nope").status_code == 401, "bad token 401")

print("full 4-turn flow")
for expect, tok in [("turn_a1", tokA), ("turn_b1", tokB), ("turn_a2", tokA), ("turn_b2", tokB)]:
    check(c.get(f"/api/rooms/{code}").get_json()["current_turn"] == expect, f"current_turn == {expect}")
    check(mpost(f"/api/rooms/{code}/turns", tok).status_code == 200, f"submit {expect} ok")
j = c.get(f"/api/rooms/{code}").get_json()
check(j["state"] == "deliberating", "after 4 turns -> deliberating")
check(len(j["turns"]) == 4 and all(t["has_audio"] for t in j["turns"]), "4 turns, all audio")

print("audio proxy")
r = c.get(f"/api/rooms/{code}/turns/turn_a1/audio", headers={"X-Debater-Token": tokA})
check(r.status_code == 200 and r.data.startswith(b"\x1a\x45\xdf\xa3"), "audio streamed back")
check(c.get(f"/api/rooms/{code}/turns/turn_a1/audio").status_code == 401, "audio requires token")

print("mutual finish (fresh room)")
r = c.post("/api/rooms", json={"topic": "موضوع آخر"})
code2, a2 = r.get_json()["code"], r.get_json()["token"]
c.post(f"/api/rooms/{code2}/claim", json={"name": "أ", "claim": "دعوى أ"}, headers={"X-Debater-Token": a2})
b2 = c.post(f"/api/rooms/{code2}/join", json={"name": "ب", "claim": "دعوى ب", "consent": True}).get_json()["token"]
c.post(f"/api/rooms/{code2}/ready", headers={"X-Debater-Token": a2})
c.post(f"/api/rooms/{code2}/ready", headers={"X-Debater-Token": b2})
c.post(f"/api/rooms/{code2}/finish", headers={"X-Debater-Token": a2})
check(c.get(f"/api/rooms/{code2}").get_json()["state"] == "turn_a1", "one finish -> still live")
c.post(f"/api/rooms/{code2}/finish", headers={"X-Debater-Token": b2})
check(c.get(f"/api/rooms/{code2}").get_json()["state"] == "deliberating", "both finish -> deliberating")

print("rate limit (limit=3)")
statuses = [c.post("/api/rooms", json={"topic": "x"}).status_code for _ in range(6)]
check(429 in statuses, f"429 appears in {statuses}")

print("reconcile: no-show forfeit + abandonment (unit)")
room = S.new_room("TESTAA", "t", "tok")
room["debaters"]["a"].update({"name": "a", "claim": "c"})
room["debaters"]["b"].update({"name": "b", "claim": "c"})
S.start_debate(room)
room["turn_deadline_at"] = S.now_utc() - timedelta(seconds=999)
check(S.reconcile(room) and room["turns"][0]["forfeited"] and room["state"] == "turn_b1", "overdue turn forfeited + advanced")
room2 = S.new_room("TESTBB", "t", "tok")
room2["last_activity_at"] = S.now_utc() - timedelta(minutes=999)
S.reconcile(room2)
check(room2["state"] == "abandoned", "idle room -> abandoned")

print(f"\nALL {ok} CHECKS PASSED")
shutil.rmtree(TMP, ignore_errors=True)
