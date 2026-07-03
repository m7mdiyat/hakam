"""QA Gate 4 — extraction fidelity over real debates (human-reviewed).

Runs the production extraction on stored Firestore rooms and prints the full
argument maps alongside the transcript, for a human to judge:
- is every extracted argument a fair (charitable) reading of what was said?
- are implicit premises genuinely implicit, minimal, and marked?
- did the empty cases (assertions / orphan premises) catch the right things?

Acceptance (design): >=90% of arguments judged faithful; zero quoted-premise
misattributions (mechanically impossible via segment ownership); zero anchor
leaks on implicit premises (structurally impossible via schema shape).

    GOOGLE_APPLICATION_CREDENTIALS=... .venv/bin/python -m backend.eval.extraction_fidelity CODE [CODE...]
"""
from __future__ import annotations

import sys

from ..extraction import resolve_rebuts, run_extraction


def dump_room(room: dict) -> None:
    names = {s: room["debaters"][s].get("name") or s for s in ("a", "b")}
    print(f"\n{'=' * 70}\nالموضوع: {room['topic']}   أ={names['a']} · ب={names['b']}")
    for idx, t in enumerate(room["turns"], 1):
        segs = (t.get("transcript") or {}).get("segments", [])
        if not segs:
            print(f"--- t{idx} ({names[t['debater']]}): (لا نص)")
            continue
        print(f"--- t{idx} ({names[t['debater']]})")
        for s in segs:
            print(f"  [t{idx}-{s['i']:02d}] {s['text']}")
    maps = resolve_rebuts({s: run_extraction(room, s) for s in ("a", "b")}, room)
    for s in ("a", "b"):
        m = maps[s]
        print(f"\n### حجج {names[s]} ({len(m['arguments'])})")
        for arg in m["arguments"]:
            c = arg["classification"]
            tags = [arg["weight"], c["type"] + (" تقريبي" if c["tentative"] else "")]
            if arg.get("rebuts"):
                tags.append(f"ترد على {arg['rebuts']['target_id']}")
            if arg.get("unanswered"):
                tags.append("بلا رد")
            print(f"[{arg['id']}] {' · '.join(tags)}")
            print(f"  نتيجة {arg['conclusion']['segment_ids']}: «{arg['conclusion']['quote']}»")
            for p in arg["premises"]:
                ext = f" [خارجي: {p['external_claim_ar']}]" if p["external"] else ""
                print(f"  مقدمة {p['segment_ids']}: «{p['quote']}»{ext}")
            for ip in arg["implicit_premises"]:
                print(f"  مضمرة: {ip['text_ar']}")
        for u in m["unsupported_assertions"]:
            print(f"  رأي بلا مقدمات: «{u['quote']}»")
        for o in m["orphan_premises"]:
            print(f"  شواهد بلا نتيجة: «{o['quote']}»")


def main(codes: list) -> int:
    from google.cloud import firestore
    db = firestore.Client(project="hakam-501212")
    for code in codes:
        room = db.collection("rooms").document(code).get().to_dict()
        if room is None:
            print(f"{code}: not found (expired?)")
            continue
        dump_room(room)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["TVL6KD"]))
