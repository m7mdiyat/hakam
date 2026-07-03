"""Live end-to-end judge smoke: a full 2-turn debate through the local API with
real Vertex calls (transcription + 4 probes + synthesis).

Debater A argues remote education widens gaps; debater B argues the opposite
and deliberately commits an ad hominem — the smoke passes if a verdict lands,
and ideally the شخصنة card comes back with a working audio anchor.

    .venv/bin/python -m backend.eval.judge_smoke

Needs ADC + HAKAM_GEMINI_ENABLED=1 (root .env). Local store/audio dirs.
"""
from __future__ import annotations

import io
import json
import subprocess
import tempfile
import time
from pathlib import Path

TURN_A = (
    "أرى أن التعليم عن بعد يوسع الفجوة بين الطلاب. "
    "السبب الأول تفاوت جودة الإنترنت بين البيوت، فطالب القرية لا يملك ما يملكه طالب المدينة. "
    "والسبب الثاني غياب الإشراف المباشر، فالطالب الضعيف يضيع بلا متابعة."
)
TURN_B = (
    "كلامك مرفوض، وأنت شخص فاشل دراسيًا ولهذا تكره التعليم عن بعد. "
    "التعليم عن بعد أتاح دروسًا مسجلة يعيدها الطالب متى شاء، "
    "وهذا يخدم الطالب الضعيف قبل غيره."
)


def _speak_m4a(text: str) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        aiff = Path(tmp) / "t.aiff"
        m4a = Path(tmp) / "t.m4a"
        subprocess.run(["say", "-v", "Majed", "-o", str(aiff), text],
                       check=True, timeout=120)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(aiff),
                        "-ac", "1", "-c:a", "aac", str(m4a)], check=True, timeout=120)
        return m4a.read_bytes()


def main() -> int:
    from backend.app import app
    client = app.test_client()

    def post(url, **kw):
        res = client.post(url, **kw)
        assert res.status_code < 400, f"{url}: {res.get_data(as_text=True)}"
        return res.get_json()

    created = post("/api/rooms", json={"topic": "التعليم عن بعد بين التوسيع والتضييق للفجوة التعليمية"})
    code, tok_a = created["code"], created["token"]
    tok_b = post(f"/api/rooms/{code}/join", json={
        "name": "سارة", "claim": "التعليم عن بعد يخدم الطالب الضعيف", "consent": True})["token"]
    post(f"/api/rooms/{code}/claim", json={"name": "أحمد", "claim": "التعليم عن بعد يوسع الفجوة"},
         headers={"X-Debater-Token": tok_a})
    for tok in (tok_a, tok_b):
        post(f"/api/rooms/{code}/ready", json={"ready": True}, headers={"X-Debater-Token": tok})

    print("recording + uploading turns (TTS)...")
    for tok, text in ((tok_a, TURN_A), (tok_b, TURN_B)):
        audio = _speak_m4a(text)
        post(f"/api/rooms/{code}/turns",
             data={"audio": (io.BytesIO(audio), "turn.m4a", "audio/mp4"),
                   "duration_ms": "20000"},
             headers={"X-Debater-Token": tok}, content_type="multipart/form-data")

    print("both request finish -> judging runs inline on the 2nd finish...")
    post(f"/api/rooms/{code}/finish", headers={"X-Debater-Token": tok_a})
    t0 = time.time()
    view = post(f"/api/rooms/{code}/finish", headers={"X-Debater-Token": tok_b})
    if view["judging_status"] != "done":  # e.g. first attempt failed -> retrigger
        view = post(f"/api/rooms/{code}/judge", headers={"X-Debater-Token": tok_a})
    elapsed = time.time() - t0

    v = view["verdict"]
    assert v, f"no verdict; judging_status={view['judging_status']}"
    print(f"\n=== verdict v{v.get('schema_version', 1)} in {elapsed:.1f}s ===")
    print(f"tier={v['tier']} winner={v['winner']} margin={v['margin']}")
    print(f"درجة الحجاج: {v['score']}  breakdown: "
          f"{ {s: v['score_breakdown'][s] for s in ('a', 'b')} }")
    for side in ("a", "b"):
        print(f"axes[{side}] =", v["scores"][side], "| emotionality:", v["emotionality"][side])
    print("diagnostics:", v["diagnostics"])
    for side in ("a", "b"):
        print(f"\n--- تحليل حجج «{side}» ---")
        for arg in v["analysis"][side]["arguments"]:
            flags = []
            if arg["rebuts"]: flags.append(f"ترد على {arg['rebuts']['target_id']} ({arg['rebuts']['effect']})")
            if arg["unanswered"]: flags.append("بقيت بلا رد")
            print(f"[{arg['id']}] {arg['weight']} · {arg['classification']['type']}"
                  f" · verdict={arg['verdict']}{' · ' + ' · '.join(flags) if flags else ''}")
            print(f"  النتيجة: «{arg['conclusion']['quote'][:70]}» audio={arg['conclusion']['audio']}")
            for p in arg["premises"]:
                print(f"  مقدمة: «{p['quote'][:70]}»" + (" [خارجية]" if p["external"] else ""))
            for ip in arg["implicit_premises"]:
                print(f"  مقدمة مضمرة: ({ip['text_ar'][:70]})")
            if arg["failure_point_ar"]:
                print(f"  موضع الخلل: {arg['failure_point_ar']}")
        for u in v["analysis"][side]["unsupported_assertions"]:
            print(f"  رأي بلا مقدمات: «{u['quote'][:70]}»")
    for f in v["fallacies"]:
        print(f"\nfallacy on «{f['speaker']}»: {f['name_ar']} ({f['severity']})"
              f" linked={f.get('argument_id')}")
        print(f"  quote: {f['quote']}")
        print(f"  audio: {f['audio']}")
    for s in v["soundness"]:
        print(f"\nsoundness on «{s['speaker']}»: {s['name_ar']} — {s['explanation_ar'][:80]}")
    for e in v["external_claims"]:
        print(f"\nexternal claim [{e['speaker']}/{e['argument_id']}]: {e['claim_ar'][:80]}")
    if v.get("key_moment"):
        print(f"\nkey moment [{v['key_moment']['turn']}]: {v['key_moment']['description_ar']}")
    print(f"\nreasoning: {v['reasoning_ar']}")
    if v.get("profiles"):
        for side in ("a", "b"):
            print(f"tip[{side}]: {v['profiles'][side]['tip_ar']}")
    print("\nJUDGE SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
