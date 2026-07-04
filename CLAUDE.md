# CLAUDE.md — Hakam (الحَكَم)

Arabic AI debate judge. Two people join a room via invite link, each states a claim, they debate in timed recorded voice turns, and Gemini judges the arguments — logic, fallacies, emotionality — delivering a structured verdict (الحُكْم).

## Environment rules (critical — read first)

- Before ANY `gcloud` command in any session, run: `export CLOUDSDK_ACTIVE_CONFIG_NAME=hakam`
- GCP project: `hakam-501212` · region: `me-central1` · bucket: `gs://hakam-audio`
- NEVER touch the default gcloud config or anything related to m7mdiyat / `handy-digit-482820-m6`
- Gemini runs via **Vertex AI with Application Default Credentials** — the same identity as Firestore/GCS (Cloud Run runtime SA needs `roles/aiplatform.user`; locally, project-scoped ADC at `~/.config/gcloud/hakam_adc.json`, wired through root `.env` `GOOGLE_APPLICATION_CREDENTIALS` + `HAKAM_GEMINI_ENABLED=1`). **There is no API key anywhere.** All model calls happen server-side in Flask; nothing model-related ever appears in frontend code, logs, or commits.

## Stack

- Frontend: Vite vanilla JS SPA (no framework), Arabic RTL (`<html dir="rtl" lang="ar">`). Fonts (self-hosted woff2 unicode-range subsets in `frontend/public/fonts/`): **IBM Plex Sans Arabic for all UI text** (Readex Pro's compact isolated ع reads as a broken letter to users — user decision 2026-07-03, supersedes the design file's font), **Readex Pro for the brand wordmark only** (`--font-brand` on `.wordmark`/`.brand-name`)
- Visual spec: `design/hakam-design.html` (source of truth) + `design/NOTES.md` (extracted tokens) — dark ink background, brass/gold accent (verdict + primary CTAs only), Debater A = teal, Debater B = coral, everywhere and consistently
- Backend: Python Flask on Cloud Run
- Room state: Firestore (native mode), `rooms` collection — never in-memory (Cloud Run instances restart/scale)
- Audio: GCS `hakam-audio`, lifecycle rule deletes objects after 2 days. Audio never stored in repo or Firestore
- Realtime sync: frontend polls `GET /api/rooms/{code}` every 2s. No WebSockets in MVP
- Hosting: **split** — frontend on GitHub Pages (`thehakam.com`), backend API-only on Cloud Run, CORS-restricted; the Cloud Run root redirects to the frontend (see «Hosting & CORS» below)
- No user accounts. First names only. Room codes: 6 chars (unambiguous set, no 0/O/1/I), expire after 24h

## Hosting & CORS (split: GitHub Pages + Cloud Run)

Frontend and backend are hosted separately — this replaced the original single-origin plan:

- **Frontend** → GitHub Pages at `thehakam.com`, built + published by `.github/workflows/deploy-pages.yml` on push to `main` touching `frontend/**`. `frontend/public/CNAME` = `thehakam.com`; Vite `base: '/'` (custom apex domain). SPA deep links (`/j/CODE`, `/r/CODE`) survive a direct load/refresh via `frontend/public/404.html` + a decode snippet in `index.html` (GitHub Pages has no server-side routing). **Live** at `https://thehakam.com` — apex `A`/`AAAA` records point at GitHub Pages, custom domain + **Enforce HTTPS** active (`http://` → 301 `https://`); `www` CNAMEs to `m7mdiyat.github.io`.
- **Backend** → API-only Flask on Cloud Run: serves `/api/*` + `/health`, and **302-redirects every other path to `HAKAM_FRONTEND_URL`** (default `https://thehakam.com`), preserving path + query so old Cloud-Run links map over. The Dockerfile no longer builds or serves the SPA.
- **Backend URL** (frontend build var): `frontend/src/api.js` calls `${VITE_API_BASE_URL}/api/...`. `frontend/.env.production` = Cloud Run URL (`https://hakam-176728126674.me-central1.run.app`), `frontend/.env.development` = `http://localhost:8080`, empty = relative `/api`. These `.env.*` hold only public URLs and ARE committed.
- **CORS** (flask-cors in `backend/app.py`): scoped to `/api/*`, restricted to `HAKAM_CORS_ORIGINS` (default `https://thehakam.com,http://localhost:5173`). Reflects the matching origin — **never `*`** — and handles preflight + the `X-Debater-Token` header; `supports_credentials=False` (bearer token in a header, no cookies).
- Health is at **`/health`** — Cloud Run's Google Front End reserves `/healthz` and 404s it before it reaches the container.
- Local dev: backend on `:8080` (`HAKAM_LOCAL=1`), Vite UI on `:5173` calling it cross-origin (CORS allows `localhost:5173`).

## Data flow

Browser MediaRecorder → `POST /api/rooms/{code}/turns` (multipart) → ffmpeg transcodes to a canonical mono `.m4a` (cross-device playback + documented Gemini input format) and measures loudness + speech-end + fine silence intervals (`audio_stats.silences`, ≥0.3s — transcript timestamps are whole-second MM:SS model output, so every audio-proof anchor SNAPS to these measured speech boundaries in `judge._anchor`; the client proof player awaits `seeked` and cuts clip ends with an rAF watcher) → **speech gate**: peak below `HAKAM_SILENCE_GATE_DB` (−50; a dead mic measures ≈ −91) → reject with `silent_audio` before storage or any model — Gemini FABRICATES fluent transcripts from silent audio + a topic, so silence must never reach it → original + m4a to GCS → **Cloud Tasks queue** → `POST /api/internal/transcribe` (OIDC-verified) → `gemini-3.5-flash` transcribes (verbatim Arabic, dialect as spoken, timestamped sentence segments; coverage validated against measured speech-end with one continue-retry, else flagged `degraded`) → transcript on room doc (**segments are the app's single time authority** — every audio-proof anchor resolves against them; the judge never sees clock time) → mutual finish → Verdict-v2 judging (below) → verdict JSON (`schema_version: 2`) → both clients render.

MediaRecorder notes: feature-detect mimeType (`audio/webm` Chrome/Android, `audio/mp4` iOS Safari) and send whichever exists. Recording is **tap-to-toggle**: one tap starts (and stamps the server speaking clock via `turns/start`), the next stops and sends; a live waveform meter makes a dead mic visibly flat. Cap turn length server-side by TRIMMING the canonical m4a to `TURN_SECONDS + AUDIO_DURATION_GRACE` (ffmpeg `-t`), never by rejecting — a late auto-stop (throttled tab, suspended phone) keeps its legitimate window instead of losing the turn.

Turn-timing invariants (the «لم تُسجَّل» class of bugs — a full-length take can only START uploading after the deadline): `SUBMIT_GRACE_SECONDS` (45) bounds upload *lateness* only, never content length; a STARTED turn forfeits only at deadline + SUBMIT_GRACE + NOSHOW_GRACE (strictly after the last acceptable submit — reconcile runs on either side's 2s poll and must never kill an in-flight upload); prep no-show keeps the short grace. Client side: the recorder re-arms its auto-stop from the server clock on every poll (`syncStop` — the local timer starts late by the getUserMedia/permission delay), re-fires idempotent `turns/start` if the first was lost (else prep expiry forfeits mid-recording), records timesliced + holds a screen wake lock, and retries uploads on network/5xx (never 4xx).

Mic-stream rule: the stream is acquired ONCE per debate (`acquireMic` in recorder.js) and reused across takes — browsers with one-time grants (Chrome «Allow this time», Safari per-session) re-prompt on the next getUserMedia after tracks are stopped, so NEVER stop the shared stream's tracks mid-debate: disable between takes, `releaseMic()` only on debate-screen unmount. `warmMic()` pre-opens it on mount iff permission is already granted (never prompts). Plain `{audio:true}` capture is OS-mixed — holding it open does not lock the mic from other applications (iOS's one-capturing-app limit is the OS, not us).

## Room state machine

`lobby → claims → turn_a1 → turn_b1 → … → deliberating → verdict`
Rounds per side are **per-room (1–3, default 2)**, picked by the creator in the lobby via `POST format` (changing resets both ready flags). Plus `abandoned` (no activity 30 min). Each turn opens **unstarted** with a prep window (`HAKAM_PREP_SECONDS`, 120s): the speaking clock starts only when the debater taps the mic (`POST turns/start`, server-stamped — chess-clock model: opening silence costs the speaker); never tapping forfeits at prep expiry like a no-show. Server is the source of truth for whose turn it is and all deadlines; clients render, never decide.

## API

- `POST /api/rooms` `{topic}` → `{code, token}` (creator = debater A)
- `POST /api/rooms/{code}/join` `{name, claim, consent: true}` → `{token}` (debater B; consent checkbox required)
- `POST /api/rooms/{code}/claim` `{name, claim}` (A sets theirs)
- `POST /api/rooms/{code}/format` `{rounds_per_side}` — creator only, pre-debate only; resets ready flags
- `POST /api/rooms/{code}/ready`
- `POST /api/rooms/{code}/turns/start` — turn holder taps the mic; server stamps the speaking clock (idempotent)
- `POST /api/rooms/{code}/turns` (multipart audio) — server validates turn, duration, and the speech gate
- `GET /api/rooms/{code}` → full public state (poll target; includes transcripts, `judging_status`, verdict). Clients send their token here too: the server keeps a throttled per-debater `last_seen` (`HAKAM_PRESENCE_BUMP_SECONDS`=8 — the 2s poll never writes Firestore per request) and exposes `debaters[side].online` (unseen > `HAKAM_PRESENCE_TTL_SECONDS`=15 → offline; never-seen = null = treat as online)
- `GET /api/rooms/{code}/turns/{turn}/audio` — serves the canonical m4a (participants only, proxied)
- `POST /api/rooms/{code}/finish` — flags agreement; judging runs inline when both have flagged
- `POST /api/rooms/{code}/judge` — lease-guarded retrigger (clients fire it when polling shows judging null/failed/stale)
- `POST /api/internal/transcribe` — Cloud Tasks worker target (OIDC audience + SA email verified in-app)

Each debater gets a random token at create/join, sent as `X-Debater-Token` header. Rate-limit room creation per IP.

## Judging rules (Verdict v2 — the argument-structure judge)

- Model: `gemini-3.5-flash` for EVERYTHING (transcription, extraction, probes, synthesis) via Vertex ADC; temperature 0; structured output with `propertyOrdering` (analysis fields first, `winner` LAST — reason before deciding); Arabic output; verify model IDs against live docs on change, never from memory.
- Judge argumentation quality ONLY — tabula rasa: cited statistics/sources are evaluated as claims inside the argument's structure, never fact-checked.
- Anonymization is SERVER CODE, not a prompt: labels «أ»/«ب», names token-stripped from everything model-bound; real names are injected into display text only after generation. Pinned by a test that captures every prompt.
- Pipeline (~7 Flash calls, ~20-30s): **extract once, evaluate four ways.**
  1. *Extraction* (`backend/extraction.py`): one call per debater, the target always labeled «أ» with claim first (label/order-invariant — extraction makes no comparative judgment). ≤4 arguments each (one رئيسية): conclusion + premises quoted VERBATIM with segment ids (validated to anchor inside ONE target-owned turn — fabricated quotes drop the argument); implicit premises are a separate id-less schema shape (an anchor leak is structurally impossible) and display as the judge's inference; rebuttals reference opponent SEGMENTS, resolved server-side to argument ids by overlap + temporal rule (an argument exists from its CONCLUSION's turn). Empty cases are first-class: «قدّم رأيًا بلا مقدمات تدعمه» + orphan premises.
  2. *Evaluation ensemble*: 4 probes (2×2 label mapping × claims order, the position-bias control) judge the SAME map — per-argument verdicts (deductive → سليم/مختل البناء with موضع الخلل; inductive → قوية/ضعيفة), rebuttal effects, classification votes, fallacies LINKED to arguments, soundness findings, extraction audit flags, plus the 5 legacy axes.
  3. *Merge* (deterministic server code): verdicts ≥3/4 else «تقييم متقارب»; rebuttal effects by ordinal median; classification override ≥3/4; fallacy/soundness clustering ≥3/4; audit flags ≥2/4 trigger ONE extraction repair round.
  4. *Synthesis*: one call narrating the ALREADY-merged verdict (cannot re-judge) → reasoning, key moment, tips.
- Scoring — **درجة الحجاج** (`backend/scoring.py`, deterministic; constants are Gate-3 priors): `score = 100·Q − 25·U − deductions`. Q = best-prefix quality over surviving argument credits (valid 1.0 / strong 0.9 / contested 0.5 / invalid-weak 0.35; RANK_W 1/.5/.25/.125 with max-over-k — **one airtight argument hits the ceiling, extra arguments never dilute**). U = quality-weighted fraction of the opponent's ANSWERABLE case left unaddressed (attempted rebuttals count even when they fail — EXCEPT rebuttals carrying a consensus رجل قش card). Deductions: fallacies with linkage-derived severity (primary→high −8, secondary→medium −5, floating→low −2) + soundness (تناقض ذاتي −10, تخلٍّ عن الدعوى −8, ادعاء مفصلي بلا سند −6, انزياح −5). **External-claim truth affects NOTHING** (locked): the registry flags «وقائع استند إليها القول» without ruling on them.
- The 5 axes (الاتساق المنطقي، الالتزام بالموضوع، الرد على النقاط، الوضوح، الهدوء والعقلانية) survive median-merged as a demoted strip AND a cross-check: axes disagreeing in sign with درجة الحجاج forces «متقاربة». Rebuttal axis is mechanically excluded (null) for a debater with no turn after any opponent turn.
- Tier (عالية/متوسطة/متقاربة) is computed from ensemble behavior ONLY — winner votes (per-probe structured scores), margin bands (<3 forced متقاربة · 3–6 · 7–14 · ≥15), axis spread, contested/audit/incoherence counts. Model self-reported confidence never gates anything.
- Fallacies: closed 14-type taxonomy; ≥3/4 consensus + verbatim/fuzzy quote anchor **or no card at all**; 3 displayed cards per debater max; straw man is structurally testable against the extracted original. Soundness (تماسك الموقف): closed 4-type taxonomy, same receipt discipline (contradiction = TWO anchored quotes).
- Settled calibration (user decisions 2026-07-03): `LINKED_FALLACY_FACTOR = 1.0` FINAL — a fallacy linked to an already-negative argument bills both penalties (two distinct offenses). **شخصنة severity is TONE-based, never linkage-based**: genuine insult to intelligence/character/dignity → medium (−5), dismissive jab → low (−2), NEVER high, and when tone is ambiguous the prompt mandates the LOWER penalty (a wrongly harsh penalty damages trust more than under-penalizing). Tone is the least mechanically verifiable judgment in the system → standing Gate-4 watch item. **Tie-break tolerance**: a clear score gap (`MARGIN_DISSENT_TOLERANT`, currently 15.0 — an explicit PLACEHOLDER, tune on the real-debate corpus before trusting it) tolerates one dissenting probe, so an 18-point winner isn't declared متقاربة over a single internal tie.
- Emotionality meter is derived (`100 − composure + 5·min(2, emotional-register fallacies)`), never a separate model output.
- QA gates in `backend/eval/` — rerun on ANY prompt/model change: Gate 1 timestamps (`timestamps.py`, corpus pending), Gate 2 injection red-team (`injection.py`, incl. injection-as-syllogism + extractor-directed cases; attacks planted in the baseline LOSER's turn so the flip check binds), Gate 3 stability (`--stability`), Gate 4 extraction fidelity (`extraction_fidelity.py CODE...` — dumps the transcript + extraction map for human fairness review). **Gate 4 is OPEN, not passed**: target ≈10 human-reviewed real debates, 2 done; extraction fidelity is the trust foundation of Verdict v2 — never claim this gate passed until the user has actually reviewed enough cases.
- Verdict doc: `schema_version: 2`; v1 verdicts render via a frontend fallback for their 24h TTL.

## Frontend screens (match /design exports exactly)

1. Landing — wordmark الحَكَم (definite article, matches thehakam.com; keep the fatḥas — distinct from الحُكْم "verdict"), tagline «لتكن الحُجّة هي الفيصل», topic input, create CTA, join-by-code link
2. Lobby — invite link + copy, two debater claim cards (teal/coral), **rounds selector** (creator-only segmented control 1–3, resets ready flags), ready gating
3. Live debate — **debate topic pinned top-center** (sticky under the header on mobile, own grid row on desktop — never scrolls away), claim chips with a pulsing «غير متصل» presence badge when the opponent's client stops polling, circular countdown (full ring + prep countdown until the mic is tapped), **tap-to-toggle mic** (solid fill + sonar rings + stop-square glyph + live elapsed timer while recording; live waveform bars under the orb), live transcript feed, ordinal round labels («الجولة الأولى» — debaters are identified by name + color, never «أ/ب» on screen), turn progress dots, mutual finish request
4. Deliberation — «الحَكَم يراجع الحجج»: the scale mark tilts like it's weighing, four step messages cycle (4.5s each), verdict interrupts instantly
5. Verdict (v2) — hero (winner + درجة الحجاج chips + reasoning) → **تحليل الحجج** (per-debater argument cards: classification chips «استدلال قطعي/ترجيحي», verdict chips, playable quoted premises/conclusions, ghost «مقدمة غير منطوقة — استنتجها الحَكَم» never playable, rebuttal cross-links, «بقيت بلا ردّ» badges — these REPLACE the old نقاط بلا رد panel, «قدّم رأيًا بلا مقدمات تدعمه» banners) → **صحة القول** (fallacy cards with «ضمن حجته — اعرضها» scroll-links, تماسك الموقف receipt cards, «وقائع استند إليها القول… لا يفصل الحَكَم في صحتها») → **التقييم العام** collapsed (5-axis bars, emotionality meters, hand-built SVG pentagon radar — NOT Chart.js) → اللحظة الفاصلة → نصيحة الحَكَم → «النص الكامل للمناظرة» collapsible (per-turn playback + full text) → share + rematch. Every claim about a debater is playable in their own voice or explicitly marked as the judge's inference.

All UI text in Arabic. Western tabular numerals for timers/scores. RTL must be real (mirrored layout/icons), not just right-aligned text.

Arabic typography gotchas (each cost a real bug):
- **NEVER apply `letter-spacing` to Arabic text** — it tears cursive joining apart (letters render disconnected). The room-code field spaces its typed Latin code but its Arabic placeholder opts out (`.input-code::placeholder { letter-spacing: normal }`).
- Hairline glyph joints (ع/ح) die at small sizes under `-webkit-font-smoothing: antialiased` at light weights — if a letter "looks cut", first check whether it's rasterization (weight/size/contrast fixes it) or the typeface's actual letterform (only a font change fixes it). Compare against the full upstream TTF before blaming rendering.
- Inputs use `font-size ≥ 16px` (also prevents iOS Safari's auto-zoom on focus).
- Beware substring checks against Arabic words in tests: «خسارة» contains «سارة».

## Build phases — do not mix them

- **Phase 1 (no AI) — SHIPPED:** rooms, invite links, claims, ready-up, server-driven turn timers, record + upload + playback, polling sync, mutual finish.
- **Phase 2 — SHIPPED (as Verdict v2):** queued transcription with live transcript, the argument-structure judge (extraction + 2×2 ensemble + درجة الحجاج scoring), full verdict screen with audio-proof receipts, rematch, speech gate.
- **Phase 3 (later, do not build now):** shareable verdict image, same-device pass-the-phone mode, Capacitor wrap, history, grounded fact-checking that plugs into the external-claims registry.

## Workflow rules

- Plan first: before implementing any feature, present the plan (files, endpoints, data shapes) and wait for approval
- Root-cause over guessing when debugging
- Local dev facts: repo `.venv` is **Python 3.9** (prod image is 3.12) — keep backend code 3.9-compatible. Tests: `.venv/bin/python -m pytest backend/tests/`. Local model calls need the project-scoped ADC + `HAKAM_GEMINI_ENABLED=1` (see Environment rules). Live smokes: `backend/eval/judge_smoke.py` (E2E TTS debate via `say -v Majed`), `backend/eval/injection.py` (Gate 2 / `--stability` Gate 3).
- Never commit: root `.env`, keys, service-account JSON, `node_modules`, `dist`, audio files. (`frontend/.env.{production,development}` hold only public backend URLs and ARE committed.)
- Deploy backend: `export CLOUDSDK_ACTIVE_CONFIG_NAME=hakam` then `gcloud run deploy hakam --source . --project hakam-501212 --region me-central1 --set-env-vars GOOGLE_CLOUD_PROJECT=hakam-501212,HAKAM_AUDIO_BUCKET=hakam-audio,HAKAM_TASKS_SA_EMAIL=hakam-tasks@hakam-501212.iam.gserviceaccount.com,HAKAM_SELF_URL=https://hakam-176728126674.me-central1.run.app` (public; on redeploys omit `--allow-unauthenticated`, access is preserved). Standing infra: Cloud Tasks queue `hakam-transcribe` (me-central1), SA `hakam-tasks@` (runtime SA has `serviceAccountUser` on it + `cloudtasks.enqueuer` + `aiplatform.user`), ffmpeg in the Docker image. No Secret Manager — Gemini auth is Vertex ADC. Frontend auto-deploys to GitHub Pages via the Actions workflow.
