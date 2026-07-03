"""QA Gate 1 — transcription timestamp accuracy (design acceptance: >=95% of
segment starts within ±2.0s of hand-labeled truth; median |error| <= 1.0s).
The signed-error bias observed here sets the final audio-proof pre-roll.

Corpus mode (the real gate):
    .venv/bin/python -m backend.eval.timestamps <corpus_dir>

  <corpus_dir> holds real recorded turns plus labels.json:
    { "clip1.webm": { "content_type": "audio/webm", "topic": "...",
                      "sentence_starts": [0.0, 6.2, 14.8, ...] }, ... }
  sentence_starts are hand-labeled true start times (~0.2s precision).

Smoke mode (no labels needed — pipeline sanity check, macOS TTS):
    .venv/bin/python -m backend.eval.timestamps --smoke

Both need gcloud ADC with access to the hakam project (see .env.example) —
model calls go through Vertex AI, no API key.
"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

from ..audio import transcode_to_m4a
from ..gemini import audio_part, generate_json
from ..prompts import TRANSCRIBE_PROMPT
from ..transcribe import TRANSCRIBE_SCHEMA, normalize_segments

WITHIN_S = 2.0  # acceptance window around a true sentence start


def transcribe_bytes(data: bytes, content_type: str, topic: str) -> "tuple[list, float]":
    m4a, duration_s, _stats = transcode_to_m4a(data, content_type)
    result = generate_json(
        TRANSCRIBE_PROMPT.format(topic=topic),
        TRANSCRIBE_SCHEMA,
        parts=[audio_part(m4a, "audio/mp4")],
        thinking_budget=0,
    )
    return normalize_segments(result.get("segments"), duration_s), duration_s


def run_corpus(corpus_dir: Path) -> int:
    labels = json.loads((corpus_dir / "labels.json").read_text(encoding="utf-8"))
    errors = []  # signed: predicted_start - true_start, per true sentence start
    for fname, meta in labels.items():
        data = (corpus_dir / fname).read_bytes()
        segments, duration = transcribe_bytes(
            data, meta.get("content_type", "audio/webm"), meta.get("topic", ""))
        starts = [s["start_s"] for s in segments]
        print(f"\n{fname}: {duration:.1f}s, {len(segments)} segments")
        for true_start in meta["sentence_starts"]:
            err = min((p - true_start for p in starts), key=abs) if starts else float("inf")
            errors.append(err)
            flag = "" if abs(err) <= WITHIN_S else "   <-- MISS"
            print(f"  true {true_start:7.1f}s   nearest pred err {err:+6.2f}s{flag}")

    n = len(errors)
    within = sum(1 for e in errors if abs(e) <= WITHIN_S)
    print(f"\n=== Gate 1 ({n} labeled sentence starts) ===")
    print(f"within ±{WITHIN_S}s : {within}/{n} = {100 * within / n:.1f}%   (need >=95%)")
    print(f"median |err|  : {statistics.median(abs(e) for e in errors):.2f}s   (need <=1.0s)")
    print(f"mean signed   : {statistics.mean(errors):+.2f}s   (late bias -> raise pre-roll)")
    print(f"p95 signed    : {sorted(errors)[max(0, int(0.95 * n) - 1)]:+.2f}s")
    passed = within / n >= 0.95 and statistics.median(abs(e) for e in errors) <= 1.0
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


SMOKE_TEXT = (
    "أرى أن التعليم عن بعد يوسع الفجوة بين الطلاب ولا يضيقها. "
    "السبب الأول هو تفاوت جودة الإنترنت بين البيوت. "
    "والسبب الثاني غياب الإشراف المباشر من المعلم. "
    "ولهذا أطالب بالعودة الكاملة إلى التعليم الحضوري."
)


def run_smoke() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        aiff = Path(tmp) / "smoke.aiff"
        subprocess.run(["say", "-v", "Majed", "-o", str(aiff), SMOKE_TEXT],
                       check=True, timeout=120)
        segments, duration = transcribe_bytes(
            aiff.read_bytes(), "audio/aiff", "التعليم عن بعد")
    print(f"smoke clip: {duration:.1f}s -> {len(segments)} segments")
    for s in segments:
        print(f"  [{s['start_s']:6.1f} - {s['end_s']:6.1f}]  {s['text']}")
    ok = bool(segments) and segments[-1]["end_s"] <= duration + 2.0
    print("SMOKE OK" if ok else "SMOKE FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--smoke" in sys.argv:
        raise SystemExit(run_smoke())
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    raise SystemExit(run_corpus(Path(sys.argv[1])))
