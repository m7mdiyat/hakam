#!/usr/bin/env python3
"""مرصد المناظرات — local study dashboard for tuning the Hakam judgement.

Double-click launcher: ~/Documents/Comments/Hakam Debates.command.
Reads the rooms + shared collections with the hakam ADC and serves an Arabic
RTL dashboard at http://localhost:8788, built for ONE job: reviewing verdicts
to tune the judge. Per debate it shows «لماذا هذه النتيجة؟» — an exact-points
trace (every credit, survival factor, and deduction with its reference and the
constant that produced it, mirrored from backend/scoring.py + judge.decide_tier
and cross-checked against the stored breakdown), the argument receipts with
playable clip windows, the delivery axes (with the axes-vs-structure conflict
called out), the transcript, and a review-notes box.

Read-only against Firestore. Review status + tuning notes live in the
browser's localStorage (exportable as JSON). Reload the page to re-fetch.
Room docs hold live debater tokens + device-IP SDP blobs, so `sanitize`
strips secrets before anything reaches the page.
"""
import json
import os
import sys
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from backend.scoring import (ASSERTION_FLOOR, CREDIT, DEDUCT_FALLACY,   # noqa: E402
                             DEDUCT_SOUNDNESS, ENGAGE_MAX, SURVIVAL,
                             UNTESTED_FACTOR, construction_quality,
                             engagement_u)
from backend.judge import (MARGIN_DISSENT_TOLERANT, MARGIN_FORCED_CLOSE,  # noqa: E402
                           MARGIN_HIGH, SPREAD_CLOSE_MIN, SPREAD_HIGH_MAX)

PROJECT = "hakam-501212"
PORT = 8788
FRONTEND = "https://thehakam.com"
ADC = os.path.expanduser("~/.config/gcloud/hakam_adc.json")
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", ADC)

# (code, turn) -> gs:// uri of the canonical m4a; refreshed on every fetch.
AUDIO_URIS = {}
_audio_cache = {}


# --------------------------------------------------------------------------
# Score trace — mirrors judge._score_inputs + scoring.compute_score from the
# verdict's DISPLAY map, then cross-checks against the stored breakdown.
# The stored score may use ensemble MEANS (credit_mean/survival_mean) that the
# verdict doc doesn't keep, so each component says whether the categorical
# reconstruction matched; the stored numbers stay the authority either way.
# --------------------------------------------------------------------------
def _match(calc, stored):
    return stored is not None and abs(calc - stored) < 0.02


def _side_trace(side, an, fallacies, soundness, breakdown):
    other = "b" if side == "a" else "a"
    my = (an.get(side) or {}).get("arguments") or []
    opp = (an.get(other) or {}).get("arguments") or []
    strawman = {f["argument_id"] for f in fallacies
                if f["type"] == "straw_man" and f.get("argument_id")}

    # Survival suffered from the opponent's (non-strawman) rebuttals.
    suffered = {}
    for oa in opp:
        r = oa.get("rebuts")
        if r and oa["id"] not in strawman and r.get("effect") in ("defeated", "weakened"):
            f = SURVIVAL[r["effect"]]
            cur = suffered.get(r["target_id"])
            if cur is None or f < cur["factor"]:
                suffered[r["target_id"]] = {"kind": "rebutted", "by": oa["id"],
                                            "effect": r["effect"], "factor": f}

    items, credits = [], []
    for a in my:
        base = CREDIT.get(a["verdict"], 0.5)
        src = suffered.get(a["id"])
        factor = src["factor"] if src else 1.0
        if a.get("preempted"):
            pf = SURVIVAL.get(a["preempted"].get("effect"), 1.0)
            if pf < factor:
                factor, src = pf, {"kind": "preempted",
                                   "effect": a["preempted"]["effect"], "factor": pf}
        if a.get("untested") and UNTESTED_FACTOR < factor:
            factor, src = UNTESTED_FACTOR, {"kind": "untested",
                                            "factor": UNTESTED_FACTOR}
        credit = base * factor
        credits.append(credit)
        items.append({"id": a["id"], "weight": a["weight"], "verdict": a["verdict"],
                      "quote": (a.get("conclusion") or {}).get("quote", ""),
                      "base": base, "factor": round(factor, 2), "src": src,
                      "credit": round(credit, 3)})

    q = construction_quality(credits)
    q_floor = False
    if q == 0.0 and (an.get(side) or {}).get("unsupported_assertions"):
        q, q_floor = ASSERTION_FLOOR, True

    # Engagement duty: which answerable opponent arguments did we address?
    my_rebuts = {a["rebuts"]["target_id"] for a in my
                 if a.get("rebuts") and a["id"] not in strawman}
    opp_items = []
    for oa in opp:
        answerable = not (oa.get("untested") or oa.get("preempted"))
        opp_items.append({"id": oa["id"], "credit": CREDIT.get(oa["verdict"], 0.5),
                          "answerable": answerable, "addressed": oa["id"] in my_rebuts,
                          "strawman_voided": bool(oa["id"] in
                              {a["rebuts"]["target_id"] for a in my
                               if a.get("rebuts") and a["id"] in strawman})})
    u = engagement_u([{k: o[k] for k in ("credit", "answerable", "addressed")}
                      for o in opp_items])

    ded_items = []
    for f in fallacies:
        if f["speaker"] == side:
            ded_items.append({"kind": "fallacy", "name": f["name_ar"],
                              "severity": f["severity"], "ref": f.get("argument_id"),
                              "points": DEDUCT_FALLACY.get(f["severity"], 0.0)})
    for s in soundness:
        if s["speaker"] == side:
            ded_items.append({"kind": "soundness", "name": s["name_ar"],
                              "ref": s.get("argument_id"),
                              "points": DEDUCT_SOUNDNESS.get(s["type"], 0.0)})
    ded = sum(d["points"] for d in ded_items)

    stored = breakdown.get(side) or {}
    return {
        "items": items, "q": round(q, 3), "q_floor": q_floor,
        "u": round(u, 3), "opp_items": opp_items,
        "ded": round(ded, 1), "ded_items": ded_items,
        "stored": stored,
        "q_match": _match(q, stored.get("q")),
        "u_match": _match(u, stored.get("u")),
        "ded_match": _match(ded, stored.get("deductions")),
    }


def _tier_trace(v):
    """Reproduce decide_tier's gates from the stored diagnostics, as reasons."""
    d = v.get("diagnostics") or {}
    votes = d.get("votes") or []
    margin = (v.get("margin") or {}).get("value") or 0.0
    sc = v.get("score") or {}
    sw = ("a" if sc.get("a", 0) > sc.get("b", 0)
          else "b" if sc.get("b", 0) > sc.get("a", 0) else None)
    axes = v.get("scores") or {}

    def mean(side):
        vals = [x for x in (axes.get(side) or {}).values() if x is not None]
        return sum(vals) / len(vals) if vals else 0.0
    ma, mb = mean("a"), mean("b")
    lean = None if abs(ma - mb) < 1e-9 else ("a" if ma > mb else "b")
    spread = d.get("axis_spread_max") or 0.0
    valid = d.get("probes_valid") or len(votes)
    va, vb = votes.count("a"), votes.count("b")
    top_vote = "a" if va > vb else "b" if vb > va else None
    top_count = max(va, vb)
    has_majority = top_count > valid / 2.0
    dissents = sum(1 for x in votes if sw is not None and x != sw)
    agree = sw is not None and (
        (has_majority and top_vote == sw)
        or (margin >= MARGIN_DISSENT_TOLERANT and dissents <= 1))
    conflict = lean is not None and sw is not None and lean != sw
    conflict_fires = conflict and margin < MARGIN_HIGH

    lines = []   # [text, kind]  kind: ok | fire | watch
    lines.append(["أصوات المسابر: أ %d / ب %d من %d%s" % (
        va, vb, valid,
        "" if agree else " — لا توافق مع فارق الدرجات (يفرض متقاربة)"),
        "ok" if agree else "fire"])
    lines.append(["فارق الدرجات %.1f — حد الإجبار على «متقاربة» هو %.0f (MARGIN_FORCED_CLOSE)"
                  % (margin, MARGIN_FORCED_CLOSE),
                  "ok" if margin >= MARGIN_FORCED_CLOSE else "fire"])
    if spread > SPREAD_CLOSE_MIN:
        lines.append(["تشتت المحاور %.1f > %.0f (SPREAD_CLOSE_MIN) — يفرض متقاربة"
                      % (spread, SPREAD_CLOSE_MIN), "fire"])
    if conflict:
        who = "أ" if lean == "a" else "ب"
        if conflict_fires:
            lines.append(["المحاور مالت إلى «%s» (%.1f مقابل %.1f) عكس فارق الدرجات، "
                          "والفارق %.1f < %.0f (MARGIN_HIGH) — فرضت «متقاربة»"
                          % (who, max(ma, mb), min(ma, mb), margin, MARGIN_HIGH), "fire"])
        else:
            lines.append(["المحاور مالت إلى «%s» (%.1f مقابل %.1f) عكس فارق الدرجات — "
                          "لم تفرض «متقاربة» لأن الفارق %.1f ≥ %.0f (MARGIN_HIGH). نقطة معايرة."
                          % (who, max(ma, mb), min(ma, mb), margin, MARGIN_HIGH), "watch"])
    tier = v.get("tier")
    if tier == "medium":
        why_not_high = []
        if not (top_count == valid == 4):
            why_not_high.append("الأصوات ليست إجماع 4/4")
        if margin < MARGIN_HIGH:
            why_not_high.append("الفارق %.1f < %.0f" % (margin, MARGIN_HIGH))
        if spread > SPREAD_HIGH_MAX:
            why_not_high.append("تشتت المحاور %.1f > %.0f" % (spread, SPREAD_HIGH_MAX))
        if d.get("incoherent_probes"):
            why_not_high.append("مسابر غير متسقة داخليًا: %d" % d["incoherent_probes"])
        if d.get("contested_args"):
            why_not_high.append("حجج متنازع عليها: %d" % d["contested_args"])
        if d.get("audit_flags"):
            why_not_high.append("أعلام تدقيق: %d" % d["audit_flags"])
        if d.get("repaired"):
            why_not_high.append("أُعيد الاستخراج")
        lines.append(["ليست «عالية» لأن: " + ("، ".join(why_not_high) or "؟"), "ok"])
    if tier == "high":
        lines.append(["استوفت كل شروط «العالية»: إجماع 4/4، فارق ≥ %.0f، تشتت ≤ %.0f، "
                      "لا تنازع ولا تدقيق ولا إصلاح" % (MARGIN_HIGH, SPREAD_HIGH_MAX), "ok"])
    return {"lines": lines, "axes_mean": {"a": round(ma, 1), "b": round(mb, 1)},
            "lean": lean}


def build_trace(v):
    if not v or v.get("schema_version") != 2:
        return None
    an = v.get("analysis") or {}
    fal = v.get("fallacies") or []
    snd = v.get("soundness") or []
    bk = v.get("score_breakdown") or {}
    sides = {s: _side_trace(s, an, fal, snd, bk) for s in ("a", "b")}
    # Which component actually decided the margin?
    sa, sb = bk.get("a") or {}, bk.get("b") or {}
    parts = [("جودة البناء (Q)", 100.0 * abs((sa.get("q") or 0) - (sb.get("q") or 0))),
             ("واجب الرد (U)", ENGAGE_MAX * abs((sa.get("u") or 0) - (sb.get("u") or 0))),
             ("الخصومات", abs((sa.get("deductions") or 0) - (sb.get("deductions") or 0)))]
    parts.sort(key=lambda p: -p[1])
    decisive = parts[0][0] if parts[0][1] > 0.05 else None
    return {"sides": sides, "tier": _tier_trace(v), "decisive": decisive}


CONSTANTS_NOTE = [
    ("قيد الحجة حسب حكمها", "سليم 1.0 · قوية 0.9 · متقاربة 0.5 · مختل/ضعيفة 0.35", "scoring.py CREDIT"),
    ("أثر الرد", "أسقطها ×0.3 · أضعفها ×0.7", "scoring.py SURVIVAL"),
    ("قاعدة الكلمة الأخيرة", "حجة لم تُختبر ×%.1f" % UNTESTED_FACTOR, "scoring.py UNTESTED_FACTOR"),
    ("سقف الإهمال", "U يخصم حتى %.0f نقطة" % ENGAGE_MAX, "scoring.py ENGAGE_MAX"),
    ("خصومات المغالطات", "شديدة −8 · متوسطة −5 · طفيفة −2", "scoring.py DEDUCT_FALLACY"),
    ("خصومات التماسك", "تناقض −10 · تخلٍّ −8 · بلا سند −6 · انزياح −5", "scoring.py DEDUCT_SOUNDNESS"),
    ("حدود الطبقة", "متقاربة إن كان الفارق < %.0f · تعارض المحاور يفرضها إن كان الفارق < %.0f · تسامح مخالف واحد فوق %.0f"
     % (MARGIN_FORCED_CLOSE, MARGIN_HIGH, MARGIN_DISSENT_TOLERANT), "judge.py MARGIN_*"),
]


# --------------------------------------------------------------------------
# Firestore fetch + sanitization
# --------------------------------------------------------------------------
def _sanitize(d):
    """Room doc -> page-safe dict. Tokens and SDP blobs must never reach HTML
    that might be screenshared: rooms under 24h still accept those tokens."""
    room = {k: v for k, v in d.items() if k not in ("secret_tokens", "rtc",
                                                    "sfu_sessions", "spectators")}
    turns = []
    for t in room.get("turns") or []:
        t = dict(t)
        uri = t.pop("audio_m4a_uri", None) or t.pop("audio_uri", None)
        t.pop("audio_uri", None)
        if uri:
            AUDIO_URIS[(room["code"], t["turn"])] = uri
        t["has_audio"] = bool(uri)
        turns.append(t)
    room["turns"] = turns
    try:
        room["trace"] = build_trace(room.get("verdict"))
    except Exception as e:
        room["trace"] = None
        print("trace failed for %s: %s" % (room.get("code"), e))
    return room


def fetch():
    from google.cloud import firestore
    db = firestore.Client(project=PROJECT)
    rooms = [_sanitize(snap.to_dict()) for snap in db.collection("rooms").stream()]
    rooms.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    shared_alive = sorted(s.id for s in db.collection("shared").stream())
    return rooms, shared_alive


def _jsonable(o):
    return o.isoformat() if hasattr(o, "isoformat") else str(o)


def render_page():
    AUDIO_URIS.clear()
    rooms, shared_alive = fetch()
    payload = json.dumps(
        {"rooms": rooms, "shared_alive": shared_alive, "frontend": FRONTEND,
         "constants": CONSTANTS_NOTE,
         "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
        ensure_ascii=False, default=_jsonable).replace("</", "<\\/")
    return TEMPLATE.replace("__PAYLOAD__", payload)


# --------------------------------------------------------------------------
# Audio proxy (GCS -> browser, with Range support for Safari's <audio>)
# --------------------------------------------------------------------------
def _audio_bytes(code, turn):
    key = (code, turn)
    if key in _audio_cache:
        return _audio_cache[key]
    uri = AUDIO_URIS.get(key)
    if not uri or not uri.startswith("gs://"):
        return None
    from google.cloud import storage
    bucket_name, blob_path = uri[5:].split("/", 1)
    try:
        data = storage.Client(project=PROJECT).bucket(bucket_name) \
            .blob(blob_path).download_as_bytes()
    except Exception:
        data = None  # lifecycle-deleted (2 days) or offline
    _audio_cache[key] = data
    return data


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status, body, ctype="text/html; charset=utf-8", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            if self.path == "/" or self.path.startswith("/?"):
                try:
                    page = render_page()
                except Exception as e:
                    page = ERROR_PAGE.replace("__ERR__", str(e))
                self._send(200, page.encode("utf-8"))
            elif self.path.startswith("/audio/"):
                parts = self.path.strip("/").split("/")
                data = _audio_bytes(parts[1], parts[2]) if len(parts) == 3 else None
                if data is None:
                    self._send(410, b"gone", "text/plain")
                    return
                rng = self.headers.get("Range")
                if rng and rng.startswith("bytes="):
                    lo, _, hi = rng[6:].partition("-")
                    lo = int(lo or 0)
                    hi = min(int(hi) if hi else len(data) - 1, len(data) - 1)
                    self._send(206, data[lo:hi + 1], "audio/mp4", {
                        "Content-Range": "bytes %d-%d/%d" % (lo, hi, len(data)),
                        "Accept-Ranges": "bytes"})
                else:
                    self._send(200, data, "audio/mp4", {"Accept-Ranges": "bytes"})
            else:
                self._send(404, b"not found", "text/plain")
        except (BrokenPipeError, ConnectionResetError):
            pass


ERROR_PAGE = """<!doctype html><html dir="rtl" lang="ar"><meta charset="utf-8">
<body style="background:#0B0B0E;color:#F2EFE9;font-family:-apple-system,'SF Arabic',sans-serif;
display:grid;place-items:center;min-height:100vh;margin:0">
<div style="max-width:520px;text-align:center;padding:24px">
<div style="font-size:40px">⚖</div><h2>تعذّر جلب البيانات</h2>
<p style="color:#8A867E;direction:ltr;font-size:13px">__ERR__</p>
<p style="color:#C9C5BC">تأكد من الاتصال بالإنترنت ومن وجود بيانات الاعتماد
<code style="direction:ltr">~/.config/gcloud/hakam_adc.json</code> ثم أعد تحميل الصفحة.</p>
</div></body></html>"""


# --------------------------------------------------------------------------
# The page (Arabic RTL, Hakam design tokens; renders client-side from DATA)
# --------------------------------------------------------------------------
TEMPLATE = r"""<!doctype html>
<html dir="rtl" lang="ar">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>الحَكَم — مرصد المناظرات</title>
<style>
:root{
  --bg:#0B0B0E; --surface:#16161B; --surface-2:#101015; --surface-3:#1B1B21;
  --ink:#F2EFE9; --ink-2:#C9C5BC; --ink-3:#E6E2D9; --muted:#8A867E; --muted-2:#6E6A63;
  --gold:#C9A45C; --gold-light:#D4B36A; --gold-ink:#17110A;
  --teal:#3FB8AF; --teal-light:#A9DDD9; --coral:#F2735F; --coral-light:#F5B7AC;
  --good:#7BC47F; --bad:#E5484D; --warn:#E0A64E;
  --hair:rgba(255,255,255,.10); --hair-soft:rgba(255,255,255,.07);
  --r-card:16px; --r-sm:12px; --r-xs:10px; --r-pill:999px;
}
*{box-sizing:border-box;margin:0;padding:0}
html{background:var(--bg)}
body{
  font-family:"IBM Plex Sans Arabic",-apple-system,"SF Arabic","Geeza Pro","Segoe UI",sans-serif;
  color:var(--ink-2); background:var(--bg); line-height:1.7; font-size:14.5px;
  padding-bottom:80px;
}
.num{font-variant-numeric:tabular-nums; font-feature-settings:'tnum'; direction:ltr; unicode-bidi:embed}
.wrap{max-width:1080px;margin:0 auto;padding:0 20px}
a{color:var(--gold);text-decoration:none}

header{position:sticky;top:0;z-index:30;background:rgba(11,11,14,.86);backdrop-filter:blur(14px);
  border-bottom:1px solid var(--hair-soft)}
.hbar{display:flex;align-items:center;gap:14px;padding:14px 0}
.hlogo{font-size:22px;color:var(--gold)}
.htitle{font-size:17px;font-weight:700;color:var(--ink)}
.hsub{font-size:12px;color:var(--muted)}
.hspace{flex:1}
.hbtn{border:1px solid var(--hair);background:var(--surface);color:var(--ink-2);
  border-radius:var(--r-xs);padding:7px 14px;font:inherit;font-size:12.5px;cursor:pointer}
.hbtn:hover{border-color:var(--gold);color:var(--ink)}
.hmeta{font-size:11.5px;color:var(--muted-2)}

.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:22px 0}
.tile{background:var(--surface);border:1px solid var(--hair-soft);border-radius:var(--r-card);padding:14px 16px}
.tile .v{font-size:26px;font-weight:700;color:var(--ink)}
.tile .l{font-size:12px;color:var(--muted);margin-top:2px}
.tile.gold .v{color:var(--gold)}

.insights{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:22px}
@media(max-width:760px){.insights{grid-template-columns:1fr}}
.panel{background:var(--surface);border:1px solid var(--hair-soft);border-radius:var(--r-card);padding:16px 18px}
.panel h3{font-size:13px;color:var(--muted);font-weight:600;margin-bottom:12px}
.mrow{display:grid;grid-template-columns:minmax(110px,auto) 1fr 34px;gap:10px;align-items:center;margin:7px 0;font-size:12.5px}
.mrow .bar{height:8px;border-radius:4px;background:rgba(255,255,255,.06);overflow:hidden}
.mrow .bar i{display:block;height:100%;border-radius:4px;background:var(--gold);opacity:.85}
.mrow .n{color:var(--ink-2)}

.filters{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:16px}
.search{flex:1;min-width:200px;background:var(--surface);border:1px solid var(--hair);color:var(--ink);
  border-radius:var(--r-sm);padding:9px 14px;font:inherit;font-size:16px}
.search::placeholder{color:var(--muted-2)}
.chip{border:1px solid var(--hair);background:transparent;color:var(--muted);border-radius:var(--r-pill);
  padding:5px 14px;font:inherit;font-size:12.5px;cursor:pointer;white-space:nowrap}
.chip.on{background:var(--surface-3);color:var(--ink);border-color:var(--gold)}

.card{background:var(--surface);border:1px solid var(--hair-soft);border-radius:var(--r-card);margin-bottom:12px;overflow:hidden}
.chead{display:flex;gap:14px;align-items:flex-start;padding:16px 18px;cursor:pointer}
.chead:hover{background:var(--surface-3)}
.cmain{flex:1;min-width:0}
.ctopic{font-size:15.5px;font-weight:700;color:var(--ink)}
.cmeta{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12px;color:var(--muted);margin-top:4px}
.cside{display:flex;flex-direction:column;align-items:flex-start;gap:6px}
.crow{display:flex;gap:6px;align-items:center;flex-wrap:wrap;justify-content:flex-end}
.badge{border-radius:var(--r-pill);padding:2px 11px;font-size:11.5px;border:1px solid var(--hair);color:var(--muted)}
.badge.win{background:rgba(201,164,92,.12);border-color:rgba(201,164,92,.4);color:var(--gold-light)}
.badge.rev-ok{background:rgba(123,196,127,.1);border-color:rgba(123,196,127,.35);color:var(--good)}
.badge.rev-issue{background:rgba(229,72,77,.1);border-color:rgba(229,72,77,.35);color:#F0908D}
.score-a,.score-b{font-weight:700;font-size:13px;border-radius:var(--r-pill);padding:2px 11px}
.score-a{color:var(--teal-light);background:rgba(63,184,175,.12);border:1px solid rgba(63,184,175,.32)}
.score-b{color:var(--coral-light);background:rgba(242,115,95,.12);border:1px solid rgba(242,115,95,.32)}
.na{color:var(--teal)} .nb{color:var(--coral)}
.chev{color:var(--muted-2);transition:transform .18s;margin-top:4px}
.card.open .chev{transform:rotate(90deg)}
.cbody{display:none;border-top:1px solid var(--hair-soft);padding:6px 18px 20px}
.card.open .cbody{display:block}

.sec{margin-top:20px}
.sec>h4{font-size:12.5px;font-weight:600;color:var(--gold);margin-bottom:10px;
  display:flex;align-items:center;gap:10px}
.sec>h4::after{content:"";flex:1;height:1px;background:var(--hair-soft)}

/* why-this-result trace */
.headline{background:var(--surface-2);border-right:3px solid var(--gold);border-radius:var(--r-sm);
  padding:12px 16px;font-size:14px;color:var(--ink-3)}
.tracecols{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:12px}
@media(max-width:820px){.tracecols{grid-template-columns:1fr}}
.trace{background:var(--surface-2);border:1px solid var(--hair-soft);border-radius:var(--r-sm);padding:12px 14px}
.trace.sa{border-right:3px solid var(--teal)} .trace.sb{border-right:3px solid var(--coral)}
.trace .tname{font-weight:700;font-size:14px;margin-bottom:6px;display:flex;justify-content:space-between;align-items:center}
.trace .tscore{font-size:19px;font-weight:700;color:var(--ink)}
.titem{font-size:12.5px;padding:6px 0;border-top:1px dashed var(--hair-soft)}
.titem .why{color:var(--muted);font-size:11.5px}
.titem .ref{color:var(--gold);cursor:pointer}
.trow{display:flex;justify-content:space-between;gap:8px;font-size:12.5px;padding:5px 0;border-top:1px dashed var(--hair-soft)}
.trow b{color:var(--ink)}
.formula{font-size:12px;color:var(--ink-2);margin-top:8px;padding-top:8px;border-top:1px solid var(--hair-soft)}
.mismatch{color:var(--warn);font-size:11px}
.okmark{color:var(--good)}
.tierbox{margin-top:12px;font-size:12.5px}
.tierbox .tl{padding:4px 0;display:flex;gap:8px;align-items:baseline}
.tl .dot{flex:none;width:7px;height:7px;border-radius:50%;margin-top:2px}
.tl.ok .dot{background:var(--muted-2)} .tl.fire .dot{background:var(--bad)} .tl.watch .dot{background:var(--warn)}
.tl.watch{color:var(--warn)}
.narr{font-size:12px;color:var(--muted);margin-top:10px;font-style:normal}

.cols{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:820px){.cols{grid-template-columns:1fr}}
.colh{font-size:13px;font-weight:700;margin-bottom:8px}
.arg{background:var(--surface-2);border:1px solid var(--hair-soft);border-radius:var(--r-sm);padding:12px 14px;margin-bottom:10px}
.arg.sa{border-right:3px solid var(--teal)} .arg.sb{border-right:3px solid var(--coral)}
.arg.flash{outline:2px solid var(--gold)}
.argtop{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:8px}
.vchip{border-radius:var(--r-pill);padding:1px 10px;font-size:11px;border:1px solid var(--hair)}
.v-good{color:var(--good);border-color:rgba(123,196,127,.4);background:rgba(123,196,127,.08)}
.v-bad{color:#F0908D;border-color:rgba(229,72,77,.4);background:rgba(229,72,77,.08)}
.v-mid{color:var(--muted);background:rgba(255,255,255,.04)}
.v-warn{color:var(--warn);border-color:rgba(224,166,78,.4);background:rgba(224,166,78,.08)}
.qline{display:flex;gap:8px;align-items:flex-start;margin:6px 0;font-size:13.5px;color:var(--ink-3)}
.qline .q{flex:1}
.qline .win{font-size:10.5px;color:var(--muted-2);white-space:nowrap;margin-top:3px}
.play{flex:none;width:26px;height:26px;border-radius:50%;border:1px solid var(--hair);background:var(--surface-3);
  color:var(--ink-2);cursor:pointer;font-size:10px;display:grid;place-items:center;padding:0}
.play:hover{border-color:var(--gold);color:var(--gold)}
.play.playing{background:var(--gold);color:var(--gold-ink);border-color:var(--gold)}
.play.dead{opacity:.35;cursor:default}
.ghost{color:var(--muted);font-size:12.5px;border:1px dashed var(--hair);border-radius:var(--r-xs);
  padding:4px 10px;margin:6px 0}
.subnote{font-size:11.5px;color:var(--muted-2)}
.fail{font-size:12.5px;color:#F0908D;margin-top:4px}
.pre{background:rgba(201,164,92,.06);border:1px solid rgba(201,164,92,.25);border-radius:var(--r-xs);padding:8px 12px;margin-top:8px;font-size:12.5px}

.fcard{background:var(--surface-2);border:1px solid var(--hair-soft);border-radius:var(--r-sm);padding:12px 14px;margin-bottom:10px}
.fhead{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:6px}
.fname{font-weight:700;color:var(--ink);font-size:13.5px}
.sev-high{color:#F0908D} .sev-medium{color:var(--warn)} .sev-low{color:var(--muted)}

.axrow{display:grid;grid-template-columns:130px 1fr 40px;gap:10px;align-items:center;margin:6px 0;font-size:12.5px}
.axrow .lane{display:flex;flex-direction:column;gap:4px}
.axbar{height:8px;border-radius:4px;background:rgba(255,255,255,.06);position:relative}
.axbar i{position:absolute;inset-inline-start:0;top:0;height:100%;border-radius:4px}
.axbar.a i{background:var(--teal)} .axbar.b i{background:var(--coral)}
.axv{display:flex;flex-direction:column;gap:4px;font-size:11px}
.noturn{font-size:11px;color:var(--muted-2)}

details.tsec{margin-top:20px}
details.tsec>summary{font-size:12.5px;font-weight:600;color:var(--gold);cursor:pointer;list-style:none;
  display:flex;align-items:center;gap:10px}
details.tsec>summary::after{content:"";flex:1;height:1px;background:var(--hair-soft)}
details.tsec>summary::before{content:"▸";font-size:10px;transition:transform .15s}
details.tsec[open]>summary::before{transform:rotate(90deg)}
.turnblk{background:var(--surface-2);border:1px solid var(--hair-soft);border-radius:var(--r-sm);padding:12px 14px;margin-top:10px}
.turnhead{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:6px;font-size:13px}
.seg{color:var(--ink-3);font-size:13.5px}
.seg .si{color:var(--muted-2);font-size:9.5px;vertical-align:super;margin-inline-end:2px}
.consts{font-size:12px;color:var(--muted)}
.consts .cr{display:flex;justify-content:space-between;gap:10px;padding:4px 0;border-top:1px dashed var(--hair-soft)}
.consts .cf{color:var(--muted-2);font-size:11px}

.notes textarea{width:100%;min-height:70px;background:var(--surface-2);border:1px solid var(--hair);color:var(--ink);
  border-radius:var(--r-sm);padding:10px 12px;font:inherit;font-size:16px;resize:vertical}
.notes .rstat{display:flex;gap:8px;margin-bottom:8px}
.saved{font-size:11px;color:var(--muted-2);margin-top:4px}
.empty{color:var(--muted);text-align:center;padding:40px 0}
.claims2{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}
@media(max-width:680px){.claims2{grid-template-columns:1fr}}
.claim{border-radius:var(--r-sm);padding:9px 13px;font-size:12.5px}
.claim.a{background:rgba(63,184,175,.07);border:1px solid rgba(63,184,175,.25)}
.claim.b{background:rgba(242,115,95,.07);border:1px solid rgba(242,115,95,.25)}
</style>
</head>
<body>
<header><div class="wrap hbar">
  <span class="hlogo">⚖</span>
  <div><div class="htitle">الحَكَم — مرصد المناظرات</div>
  <div class="hsub">لماذا صدرت كل نتيجة، رقمًا رقمًا — أعد تحميل الصفحة للتحديث</div></div>
  <div class="hspace"></div>
  <span class="hmeta">آخر جلب: <span class="num" id="fetchedAt"></span></span>
  <button class="hbtn" onclick="exportNotes()">تصدير ملاحظات الضبط</button>
</div></header>

<div class="wrap">
  <div class="tiles" id="tiles"></div>
  <div class="insights">
    <div class="panel"><h3>المغالطات المرصودة (لمعايرة الكشف)</h3><div id="falPanel"></div></div>
    <div class="panel"><h3>سلوك الحسم وانحياز الموقع</h3><div id="tierPanel"></div></div>
  </div>
  <div class="filters">
    <input class="search" id="q" placeholder="ابحث في الموضوع أو الأسماء أو الرمز…" oninput="renderList()">
    <button class="chip on" data-f="all" onclick="setF(this)">الكل</button>
    <button class="chip" data-f="verdict" onclick="setF(this)">بحُكْم مكتمل</button>
    <button class="chip" data-f="close" onclick="setF(this)">متقاربة</button>
    <button class="chip" data-f="nov" onclick="setF(this)">بلا حُكْم</button>
    <button class="chip" data-f="unrev" onclick="setF(this)">لم تُراجَع بعد</button>
  </div>
  <div id="list"></div>
</div>

<script>
const DATA = __PAYLOAD__;
const AX = {logic:"الاتساق المنطقي", relevance:"الالتزام بالموضوع", rebuttal:"الرد على النقاط",
            clarity:"الوضوح", composure:"الهدوء والعقلانية"};
const TIER = {high:"حسم عالٍ", medium:"حسم متوسط", close:"نتيجة متقاربة"};
const VCH = {valid:["سليم البناء","v-good"], strong:["قوية","v-good"], contested:["تقييم متقارب","v-mid"],
             invalid:["مختل البناء","v-bad"], weak:["ضعيفة","v-bad"]};
const EFF = {defeated:"أسقطها", weakened:"أضعفها", unaffected:"لم تتأثر"};
const SEV = {high:"شديدة", medium:"متوسطة", low:"طفيفة"};
const STATE_AR = r => {
  if (r.verdict) return null;
  if (r.state==="lobby") return "بانتظار الخصم";
  if (r.state==="claims") return "في الإعداد";
  if (r.state==="abandoned") return "مهجورة";
  if (r.state.startsWith("turn_")) return "توقفت أثناء الجولات";
  if (r.state==="deliberating"){
    const js=(r.judging||{}).status;
    return js==="failed" ? "تعذّر الحُكْم" : "قيد المداولة";
  }
  return r.state;
};
let FILTER = "all";

const esc = s => String(s==null?"":s).replace(/[&<>"']/g, c =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const nm = (r,s) => esc((r.debaters[s]||{}).name || (s==="a"?"الطرف الأول":"الطرف الثاني"));
const cls = s => s==="a"?"na":"nb";
const rev = c => { try{return JSON.parse(localStorage.getItem("hakam-study:"+c))||{}}catch(e){return {}} };
const setRev = (c,o) => localStorage.setItem("hakam-study:"+c, JSON.stringify(o));
const fmt1 = v => (Math.round(v*10)/10).toString();
const dt = iso => { const d=new Date(iso); return isNaN(d)?"—":
  d.toLocaleDateString("en-GB")+" "+d.toLocaleTimeString("en-GB",{hour:"2-digit",minute:"2-digit"}); };

/* ---------- audio proof player ---------- */
const players = {};
let activeBtn = null, rafId = null;
function stopActive(){
  if (rafId) cancelAnimationFrame(rafId), rafId=null;
  if (activeBtn){ const p = players[activeBtn.dataset.k]; if (p) p.pause();
    activeBtn.classList.remove("playing"); activeBtn.textContent="▶"; activeBtn=null; }
}
function playClip(btn){
  if (btn.classList.contains("dead")) return;
  if (activeBtn===btn){ stopActive(); return; }
  stopActive();
  const {code,turn}=btn.dataset, s=+btn.dataset.s, e=+btn.dataset.e, k=code+":"+turn;
  btn.dataset.k=k;
  let p = players[k];
  if (!p){ p = players[k] = new Audio("/audio/"+code+"/"+turn);
    p.addEventListener("error",()=>{ document.querySelectorAll('.play[data-code="'+code+'"][data-turn="'+turn+'"]')
      .forEach(b=>{b.classList.add("dead");b.title="انتهى الاحتفاظ بالتسجيل (يُحذف بعد يومين)";b.textContent="✕";});
      if(activeBtn&&activeBtn.dataset.k===k)activeBtn=null; }); }
  activeBtn=btn; btn.classList.add("playing"); btn.textContent="■";
  const seekPlay=()=>{ p.currentTime=s; p.play().catch(()=>stopActive());
    const watch=()=>{ if(activeBtn!==btn)return;
      if(p.currentTime>=e-0.03||p.ended){stopActive();return;} rafId=requestAnimationFrame(watch); };
    rafId=requestAnimationFrame(watch); };
  if (p.readyState>=1) seekPlay(); else p.addEventListener("loadedmetadata",seekPlay,{once:true});
}
const playBtn=(code,a,label)=>{
  if(!a) return "";
  return '<button class="play" data-code="'+code+'" data-turn="'+a.turn+'" data-s="'+a.start_s+
    '" data-e="'+a.end_s+'" onclick="event.stopPropagation();playClip(this)" title="'+(label||"تشغيل المقطع")+'">▶</button>';
};
const winTxt=a=>a?'<span class="win num">'+fmt1(a.start_s)+"s → "+fmt1(a.end_s)+"s</span>":"";

/* ---------- aggregates ---------- */
function verdictRooms(){ return DATA.rooms.filter(r=>r.verdict); }
function renderTiles(){
  const vs=verdictRooms(), n=DATA.rooms.length;
  const close=vs.filter(r=>r.verdict.tier==="close").length;
  const reviewed=vs.filter(r=>rev(r.code).status).length;
  document.getElementById("tiles").innerHTML=[
    ["المناظرات",n,""],["بحُكْم مكتمل",vs.length,""],["نتائج متقاربة",close,""],
    ["روجعت للضبط",reviewed+" / "+vs.length,"gold"]
  ].map(t=>'<div class="tile '+t[2]+'"><div class="v num">'+t[1]+'</div><div class="l">'+t[0]+'</div></div>').join("");
}
function renderPanels(){
  const vs=verdictRooms();
  const counts={};
  vs.forEach(r=>(r.verdict.fallacies||[]).forEach(f=>counts[f.name_ar]=(counts[f.name_ar]||0)+1));
  const rows=Object.entries(counts).sort((a,b)=>b[1]-a[1]).slice(0,8);
  const mx=rows.length?rows[0][1]:1;
  document.getElementById("falPanel").innerHTML= rows.length ?
    rows.map(([k,v])=>'<div class="mrow"><span>'+esc(k)+'</span><span class="bar"><i style="width:'+
      (v/mx*100)+'%"></i></span><span class="n num">'+v+'</span></div>').join("")
    : '<div class="subnote">لا مغالطات مرصودة بعد.</div>';
  const tiers={high:0,medium:0,close:0}; let aw=0,bw=0,sa=0,sb=0;
  vs.forEach(r=>{ tiers[r.verdict.tier]=(tiers[r.verdict.tier]||0)+1;
    if(r.verdict.winner==="a")aw++; if(r.verdict.winner==="b")bw++;
    sa+=(r.verdict.score||{}).a||0; sb+=(r.verdict.score||{}).b||0; });
  const nn=vs.length||1;
  document.getElementById("tierPanel").innerHTML=
    Object.entries(tiers).map(([k,v])=>'<div class="mrow"><span>'+TIER[k]+'</span><span class="bar"><i style="width:'+
      (v/nn*100)+'%"></i></span><span class="n num">'+v+'</span></div>').join("")+
    '<div class="subnote" style="margin-top:10px">فوز البادئ (أ): <b class="num">'+aw+'</b> · فوز الثاني (ب): <b class="num">'+bw+
    '</b><br>متوسط درجة البادئ <b class="num">'+fmt1(sa/nn)+'</b> مقابل الثاني <b class="num">'+fmt1(sb/nn)+
    '</b> — راقب هذا الفرق لضبط عدالة الكلمة الأخيرة.</div>';
}

/* ---------- لماذا هذه النتيجة؟ ---------- */
function factorTxt(r, src){
  if(!src) return '<span class="why">لم يمسّها شيء (×1.0)</span>';
  if(src.kind==="rebutted")
    return '<span class="why">'+(src.effect==="defeated"?"أسقطها":"أضعفها")+' ردُّ الخصم '+
      '<span class="ref num" onclick="goArg(\''+r.code+'\',\''+src.by+'\')">'+src.by+'</span> (×'+src.factor+')</span>';
  if(src.kind==="preempted")
    return '<span class="why">عالجها الخصم مسبقًا — '+(src.effect==="defeated"?"أسقطها":"أضعفها")+' (×'+src.factor+')</span>';
  return '<span class="why">لم تُختبر — قاعدة الكلمة الأخيرة (×'+src.factor+')</span>';
}
function traceBox(r, s){
  const t=r.trace.sides[s], st=t.stored||{}, other=s==="a"?"b":"a";
  let h='<div class="trace s'+s+'"><div class="tname"><span class="'+cls(s)+'">'+nm(r,s)+
    '</span><span class="tscore num">'+(st.score!=null?st.score:"—")+'</span></div>';
  t.items.forEach(it=>{
    const v=VCH[it.verdict]||[it.verdict,"v-mid"];
    h+='<div class="titem"><span class="ref num" onclick="goArg(\''+r.code+'\',\''+it.id+'\')">'+it.id+'</span>'+
      (it.weight==="primary"?" ★":"")+' «'+esc(it.quote.slice(0,60))+(it.quote.length>60?"…":"")+'»<br>'+
      '<span class="num">'+v[0]+' ('+it.base+') × '+it.factor+' = <b>'+it.credit+'</b></span> — '+factorTxt(r,it.src)+'</div>';
  });
  if(!t.items.length) h+='<div class="titem why">لا حجج مكتملة'+(t.q_floor?' — أرضية الدعاوى Q = 0.15 (ASSERTION_FLOOR)':'')+'</div>';
  h+='<div class="trow"><span>Q جودة البناء (أفضل تشكيلة مرجّحة)</span><b class="num">'+(st.q!=null?st.q:t.q)+
     (t.q_match?' <span class="okmark">✓</span>':' <span class="mismatch">التركيب الفئوي '+t.q+' لا يطابق: مسابر منقسمة (متوسط الأصوات) أو حُكم بصيغة أقدم</span>')+'</b></div>';
  const left=t.opp_items.filter(o=>o.answerable&&!o.addressed);
  const voided=t.opp_items.filter(o=>o.strawman_voided);
  let utxt = left.length
    ? 'ترك بلا رد: '+left.map(o=>'<span class="ref num" onclick="goArg(\''+r.code+'\',\''+o.id+'\')">'+o.id+'</span> ('+o.credit+')').join('، ')
    : (t.opp_items.some(o=>o.answerable)?'عالج كل نقاط الخصم القابلة للرد':'لا نقاط خصم قابلة للرد — U لا يعاقبه (طُرحت بعد آخر مداخلاته)');
  if(voided.length) utxt+=' · ردّه على '+voided.map(o=>o.id).join('،')+' أُلغي (حمل رجل قش)';
  h+='<div class="trow"><span>U واجب الرد — '+utxt+'</span><b class="num">'+(st.u!=null?st.u:t.u)+
     (t.u_match?' <span class="okmark">✓</span>':' <span class="mismatch">التركيب الفئوي '+t.u+' لا يطابق (مسابر منقسمة أو صيغة أقدم)</span>')+'</b></div>';
  t.ded_items.forEach(d=>{
    h+='<div class="trow"><span>'+(d.kind==="fallacy"?"مغالطة: ":"تماسك: ")+esc(d.name)+
      (d.severity?' ('+SEV[d.severity]+')':'')+
      (d.ref?' — ضمن <span class="ref num" onclick="goArg(\''+r.code+'\',\''+d.ref+'\')">'+d.ref+'</span>':'')+
      '</span><b class="num">−'+d.points+'</b></div>';
  });
  h+='<div class="trow"><span>مجموع الخصومات</span><b class="num">'+(st.deductions!=null?st.deductions:t.ded)+
     (t.ded_match?' <span class="okmark">✓</span>':' <span class="mismatch">فئوي '+t.ded+' — تحقق من البطاقات المقصوصة</span>')+'</b></div>';
  h+='<div class="formula num" style="direction:rtl">الدرجة = 100×'+(st.q!=null?st.q:"؟")+' − 25×'+(st.u!=null?st.u:"؟")+
     ' − '+(st.deductions!=null?st.deductions:"؟")+' = <b>'+(st.score!=null?st.score:"؟")+'</b></div>';
  return h+'</div>';
}
function secWhy(r){
  const v=r.verdict, tr=r.trace;
  if(!tr) return '<div class="sec"><h4>لماذا هذه النتيجة؟</h4><div class="subnote">حُكم بصيغة قديمة — لا تتبع متاح.</div></div>';
  const winLine = v.winner
    ? 'الفائز: <b class="'+cls(v.winner)+'">'+nm(r,v.winner)+'</b> بفارق <b class="num">'+((v.margin||{}).value!=null?v.margin.value:"—")+'</b>'
    : 'بلا فائز — '+TIER[v.tier];
  const head = (v.winner?winLine+' · '+TIER[v.tier]:winLine)+(tr.decisive?' · العنصر الحاسم: <b>'+tr.decisive+'</b>':'');
  let h='<div class="sec"><h4>لماذا هذه النتيجة؟</h4><div class="headline">'+head+'</div>';
  h+='<div class="tracecols">'+traceBox(r,"a")+traceBox(r,"b")+'</div>';
  h+='<div class="tierbox"><b style="font-size:12.5px">لماذا طبقة «'+TIER[v.tier]+'»؟</b>'+
    tr.tier.lines.map(l=>'<div class="tl '+l[1]+'"><span class="dot"></span><span>'+esc(l[0])+'</span></div>').join("")+'</div>';
  if(v.reasoning_ar) h+='<div class="narr">سرد الحَكَم (عرضٌ للنتيجة، لا يقررها): «'+esc(v.reasoning_ar)+'»</div>';
  return h+'</div>';
}

/* ---------- argument receipts ---------- */
function argCard(r,side,a){
  const c=a.classification||{};
  const vch=VCH[a.verdict]||[a.verdict,"v-mid"];
  let h='<div class="arg s'+side+'" id="arg-'+r.code+'-'+a.id+'"><div class="argtop">'+
    (a.weight==="primary"?'<span class="vchip" style="color:var(--gold);border-color:rgba(201,164,92,.4)">★ رئيسية</span>':"")+
    '<span class="vchip v-mid">'+(c.type==="deductive"?"استدلال قطعي":"استدلال ترجيحي")+(c.tentative?" (متردد)":"")+'</span>'+
    '<span class="vchip '+vch[1]+'">'+vch[0]+'</span>'+
    (a.unanswered?'<span class="vchip v-warn">بقيت بلا ردّ</span>':"")+
    (a.untested?'<span class="vchip v-mid">لم تُختبر (آخر مداخلة)</span>':"")+
    '<span class="subnote num">'+a.id+'</span></div>';
  h+='<div class="qline">'+playBtn(r.code,(a.conclusion||{}).audio)+
     '<span class="q"><b>الخلاصة:</b> «'+esc((a.conclusion||{}).quote)+'»</span>'+winTxt((a.conclusion||{}).audio)+'</div>';
  (a.premises||[]).forEach(p=>{
    h+='<div class="qline">'+playBtn(r.code,p.audio)+'<span class="q">مقدمة: «'+esc(p.quote)+'»'+
       (p.external?' <span class="vchip v-mid">واقعة خارجية — لا يفصل الحَكَم فيها: '+esc(p.external_claim_ar)+'</span>':"")+
       '</span>'+winTxt(p.audio)+'</div>';});
  (a.implicit_premises||[]).forEach(ip=>{
    h+='<div class="ghost">مقدمة غير منطوقة — استنتجها الحَكَم: '+esc(ip.text_ar)+'</div>';});
  if(a.failure_point_ar) h+='<div class="fail">موضع الخلل: '+esc(a.failure_point_ar)+'</div>';
  if(a.rebuts) h+='<div class="subnote">تردّ على <span class="ref num" onclick="goArg(\''+r.code+'\',\''+a.rebuts.target_id+
     '\')">'+a.rebuts.target_id+'</span> — الأثر: <b>'+(EFF[a.rebuts.effect]||a.rebuts.effect||"—")+'</b></div>';
  if(a.preempted) h+='<div class="pre"><b>عالجها الخصم مسبقًا ('+(EFF[a.preempted.effect]||a.preempted.effect)+
     '):</b> '+esc(a.preempted.explanation_ar)+'<div class="qline">'+playBtn(r.code,a.preempted.audio)+
     '<span class="q">«'+esc(a.preempted.quote)+'»</span>'+winTxt(a.preempted.audio)+'</div></div>';
  return h+"</div>";
}
function secArgs(r){
  const an=r.verdict.analysis||{};
  const col=s=>{
    const m=an[s]||{};
    let h='<div><div class="colh '+cls(s)+'">'+nm(r,s)+'</div>';
    h+=(m.arguments||[]).map(a=>argCard(r,s,a)).join("")||'<div class="ghost">قدّم رأيًا بلا مقدمات تدعمه.</div>';
    (m.unsupported_assertions||[]).forEach(u=>{h+='<div class="qline">'+playBtn(r.code,u.audio)+
      '<span class="q subnote">دعوى بلا سند: «'+esc(u.quote)+'»</span>'+winTxt(u.audio)+'</div>';});
    return h+"</div>";};
  let h='<div class="sec"><h4>إيصالات الحجج (شغّل وطابق الاقتباس مع الصوت)</h4><div class="cols">'+col("a")+col("b")+'</div>';
  const v=r.verdict;
  let cards="";
  (v.fallacies||[]).forEach(f=>{
    cards+='<div class="fcard"><div class="fhead"><span class="'+cls(f.speaker)+'">●</span>'+
      '<span class="fname">'+esc(f.name_ar)+'</span><span class="vchip sev-'+f.severity+'">'+(SEV[f.severity]||f.severity)+
      ' −'+({high:8,medium:5,low:2}[f.severity]||0)+'</span>'+
      (f.argument_id?'<span class="subnote num">ضمن '+f.argument_id+'</span>':'<span class="subnote">عائمة −2</span>')+
      (f.found_by?'<span class="subnote num">إجماع '+f.found_by+'/4</span>':"")+'</div>'+
      '<div class="qline">'+playBtn(r.code,f.audio)+'<span class="q">«'+esc(f.quote)+'»</span>'+winTxt(f.audio)+'</div>'+
      '<div class="subnote">'+esc(f.explanation_ar)+'</div></div>';});
  (v.soundness||[]).forEach(s=>{
    cards+='<div class="fcard"><div class="fhead"><span class="'+cls(s.speaker)+'">●</span>'+
      '<span class="fname">تماسك الموقف: '+esc(s.name_ar)+'</span>'+
      (s.argument_id?'<span class="subnote num">ضمن '+s.argument_id+'</span>':"")+'</div>'+
      (s.quotes||[]).map(q=>'<div class="qline">'+playBtn(r.code,q.audio)+'<span class="q">«'+esc(q.quote)+'»</span>'+winTxt(q.audio)+'</div>').join("")+
      '<div class="subnote">'+esc(s.explanation_ar)+'</div></div>';});
  if(cards) h+='<div style="margin-top:12px"><div class="colh">بطاقات الخصومات</div>'+cards+'</div>';
  return h+'</div>';
}
function secAxes(r){
  const v=r.verdict, sc=v.scores||{}, tr=r.trace;
  const rows=Object.keys(AX).map(ax=>{
    const va=(sc.a||{})[ax], vb=(sc.b||{})[ax];
    const bar=(k,val)=> val==null
      ? '<span class="noturn">لم تتح له فرصة الرد — مستبعد</span>'
      : '<span class="axbar '+k+'"><i style="width:'+val+'%"></i></span>';
    return '<div class="axrow"><span>'+AX[ax]+'</span><span class="lane">'+bar("a",va)+bar("b",vb)+
      '</span><span class="axv"><span class="na num">'+(va==null?"—":va)+'</span><span class="nb num">'+(vb==null?"—":vb)+'</span></span></div>';
  }).join("");
  const am=tr?tr.tier.axes_mean:null;
  return '<details class="tsec"><summary>محاور جودة الإلقاء'+(am?' (متوسط أ '+am.a+' / ب '+am.b+')':'')+
    ' — لا تدخل الدرجة، فقط فحص تعارض</summary>'+
    '<div class="subnote" style="margin:8px 0">تقيس جودة الحديث نفسه (وضوح، تنظيم، هدوء) لا مصير الحجج بعد الاحتكاك — '+
    'قد يتحدث الخاسر البنيوي أفضل ويظل خاسرًا.</div>'+rows+'</details>';
}
function secTranscript(r){
  if(!(r.turns||[]).length) return "";
  const blocks=r.turns.map(t=>{
    const side=t.debater, tr=t.transcript||{};
    const round=(t.turn.match(/\d+/)||["؟"])[0];
    let status="";
    if(t.forfeited) status='<span class="vchip v-warn">مداخلة متنازَل عنها</span>';
    else if(tr.status==="ok"&&tr.degraded) status='<span class="vchip v-warn">تغطية منقوصة</span>';
    else if(tr.status==="failed") status='<span class="vchip v-bad">'+(tr.reason==="no_speech"?"لم يُسمَع كلام":"تعذّر النسخ")+'</span>';
    else if(tr.status&&tr.status!=="ok") status='<span class="vchip v-mid">'+esc(tr.status)+'</span>';
    const dur=t.duration_s!=null?'<span class="subnote num">'+fmt1(t.duration_s)+'s</span>':"";
    const full=t.has_audio?playBtn(r.code,{turn:t.turn,start_s:0,end_s:(t.duration_s||9999)},"تشغيل المداخلة كاملة"):"";
    const segs=(tr.segments||[]).map(s=>'<span class="si num">'+s.i+'</span>'+esc(s.text)).join(" ");
    return '<div class="turnblk"><div class="turnhead"><b class="'+cls(side)+'">الجولة '+round+
      ' — '+nm(r,side)+'</b>'+dur+status+full+'</div><div class="seg">'+(segs||'<span class="subnote">لا نص.</span>')+'</div></div>';
  }).join("");
  return '<details class="tsec"><summary>النص الكامل (أرقام المقاطع للمطابقة)</summary>'+blocks+'</details>';
}
function secConstants(){
  return '<details class="tsec"><summary>ثوابت المعايرة الحالية (ما تضبطه فعليًا)</summary><div class="consts">'+
    DATA.constants.map(c=>'<div class="cr"><span><b>'+esc(c[0])+':</b> '+esc(c[1])+'</span><span class="cf num">'+esc(c[2])+'</span></div>').join("")+
    '</div></details>';
}
function secReview(r){
  const st=rev(r.code);
  const btn=(val,label)=>'<button class="chip'+(st.status===val?" on":"")+'" onclick="setStatus(\''+r.code+'\',\''+val+'\')">'+label+'</button>';
  return '<div class="sec notes"><h4>مراجعة الضبط</h4>'+
    '<div class="rstat">'+btn("ok","الحُكْم سليم")+btn("issue","فيه ملاحظات")+
    '<button class="chip" onclick="setStatus(\''+r.code+'\',null)">لم تُراجَع</button></div>'+
    '<textarea placeholder="هل الاستخراج أمين؟ هل المقاطع تُشغَّل صحيحة؟ هل الدرجة والعوامل عادلة؟ أي ثابت يحتاج معايرة؟" '+
    'oninput="saveNotes(\''+r.code+'\',this)">'+esc(st.notes||"")+'</textarea>'+
    '<div class="saved" id="sv-'+r.code+'">'+(st.saved_at?"آخر حفظ: "+st.saved_at:"")+'</div></div>';
}

/* ---------- card + list ---------- */
function cardHtml(r){
  const v=r.verdict, st=rev(r.code);
  const share = r.share_id && DATA.shared_alive.includes(r.share_id)
    ? '<a class="badge" href="'+DATA.frontend+'/v/'+r.share_id+'" target="_blank" onclick="event.stopPropagation()">عرض عام ↗</a>' : "";
  let right="";
  if(v){
    right='<div class="crow">'+
      (v.winner?'<span class="badge win">الفائز: '+nm(r,v.winner)+'</span>':'<span class="badge">'+TIER[v.tier]+'</span>')+
      '<span class="score-a num">'+((v.score||{}).a!=null?(v.score||{}).a:"—")+'</span>'+
      '<span class="score-b num">'+((v.score||{}).b!=null?(v.score||{}).b:"—")+'</span></div>'+
      '<div class="crow">'+(v.winner?'<span class="badge">'+TIER[v.tier]+'</span>':"")+share+
      (st.status==="ok"?'<span class="badge rev-ok">روجعت ✓</span>':
       st.status==="issue"?'<span class="badge rev-issue">فيها ملاحظات</span>':
       '<span class="badge">لم تُراجَع</span>')+'</div>';
  } else {
    right='<div class="crow"><span class="badge">'+STATE_AR(r)+'</span></div>';
  }
  const rounds=((r.format||{}).rounds_per_side||"؟");
  return '<div class="card" id="c-'+r.code+'">'+
    '<div class="chead" onclick="toggle(\''+r.code+'\')">'+
      '<div class="cmain"><div class="ctopic">'+esc(r.topic||"بلا موضوع")+'</div>'+
      '<div class="cmeta"><span class="num">'+r.code+'</span><span class="num">'+dt(r.created_at)+'</span>'+
      '<span>جولات: <span class="num">'+rounds+'</span></span>'+
      '<span><span class="na">'+nm(r,"a")+'</span> × <span class="nb">'+nm(r,"b")+'</span></span>'+
      '</div></div>'+
    '<div class="cside">'+right+'</div><span class="chev">◀</span></div>'+
    '<div class="cbody"></div></div>';
}
function toggle(code){
  stopActive();
  const card=document.getElementById("c-"+code);
  const body=card.querySelector(".cbody");
  if(!card.classList.contains("open") && !body.innerHTML){
    const r=DATA.rooms.find(x=>x.code===code);
    body.innerHTML=(function(){
      const v=r.verdict;
      const claims='<div class="claims2">'+
        '<div class="claim a"><b>'+nm(r,"a")+':</b> '+esc((r.debaters.a||{}).claim||"—")+'</div>'+
        '<div class="claim b"><b>'+nm(r,"b")+':</b> '+esc((r.debaters.b||{}).claim||"—")+'</div></div>';
      return v ? claims+secWhy(r)+secArgs(r)+secAxes(r)+secTranscript(r)+secConstants()+secReview(r)
               : claims+secTranscript(r);
    })();
  }
  card.classList.toggle("open");
}
function goArg(code,id){
  const el=document.getElementById("arg-"+code+"-"+id);
  if(!el) return;
  el.scrollIntoView({behavior:"smooth",block:"center"});
  el.classList.add("flash");
  setTimeout(()=>el.classList.remove("flash"),1600);
}
function matches(r){
  const q=document.getElementById("q").value.trim();
  if(q){
    const hay=(r.topic||"")+" "+r.code+" "+nm(r,"a")+" "+nm(r,"b");
    if(!hay.includes(q)) return false;
  }
  if(FILTER==="verdict") return !!r.verdict;
  if(FILTER==="close") return r.verdict&&r.verdict.tier==="close";
  if(FILTER==="nov") return !r.verdict;
  if(FILTER==="unrev") return r.verdict&&!rev(r.code).status;
  return true;
}
function renderList(){
  const rooms=DATA.rooms.filter(matches);
  document.getElementById("list").innerHTML=
    rooms.map(cardHtml).join("")||'<div class="empty">لا نتائج مطابقة.</div>';
}
function setF(btn){
  FILTER=btn.dataset.f;
  document.querySelectorAll(".filters .chip[data-f]").forEach(c=>c.classList.toggle("on",c===btn));
  renderList();
}

/* ---------- review persistence ---------- */
function setStatus(code,val){
  const st=rev(code);
  st.status = val==="null"||val===null ? null : val;
  st.saved_at=new Date().toLocaleString("en-GB");
  setRev(code,st);
  renderTiles(); renderList();
  const card=document.getElementById("c-"+code);
  if(card){ toggle(code); }
}
let saveTimer=null;
function saveNotes(code,ta){
  clearTimeout(saveTimer);
  saveTimer=setTimeout(()=>{
    const st=rev(code); st.notes=ta.value; st.saved_at=new Date().toLocaleString("en-GB");
    setRev(code,st);
    const el=document.getElementById("sv-"+code);
    if(el) el.textContent="آخر حفظ: "+st.saved_at;
  },400);
}
function exportNotes(){
  const out=[];
  DATA.rooms.forEach(r=>{
    const st=rev(r.code);
    if(st.status||st.notes) out.push({code:r.code,topic:r.topic,
      winner:r.verdict?r.verdict.winner:null,tier:r.verdict?r.verdict.tier:null,
      score:r.verdict?r.verdict.score:null,review:st});
  });
  const blob=new Blob([JSON.stringify({exported_at:new Date().toISOString(),reviews:out},null,2)],
    {type:"application/json"});
  const a=document.createElement("a");
  a.href=URL.createObjectURL(blob);
  a.download="hakam-tuning-notes.json";
  a.click();
}

document.getElementById("fetchedAt").textContent=DATA.fetched_at;
renderTiles(); renderPanels(); renderList();
// Deep link: /#CODE opens that debate's card.
const hash=decodeURIComponent(location.hash.replace("#",""));
if(hash&&document.getElementById("c-"+hash)){
  toggle(hash);
  document.getElementById("c-"+hash).scrollIntoView();
}
</script>
</body>
</html>"""


def main():
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        # Already running from an earlier double-click — just open it.
        webbrowser.open("http://localhost:%d" % PORT)
        print("المرصد يعمل مسبقًا — فُتح في المتصفح.")
        return
    print("⚖  مرصد المناظرات — http://localhost:%d" % PORT)
    print("   أعد تحميل الصفحة للتحديث · أغلق هذه النافذة (أو Ctrl+C) للإيقاف.")
    webbrowser.open("http://localhost:%d" % PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
