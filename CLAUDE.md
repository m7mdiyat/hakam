# CLAUDE.md — Hakam (الحَكَم)

Arabic AI debate judge. Two people join a room via invite link, each states a claim, they debate in timed recorded voice turns, and Gemini judges the arguments — logic, fallacies, emotionality — delivering a structured verdict (الحُكْم).

## Environment rules (critical — read first)

- Before ANY `gcloud` command in any session, run: `export CLOUDSDK_ACTIVE_CONFIG_NAME=hakam`
- GCP project: `hakam-501212` · region: `me-central1` · bucket: `gs://hakam-audio`
- NEVER touch the default gcloud config or anything related to m7mdiyat / `handy-digit-482820-m6`
- `GEMINI_API_KEY` comes from `.env` locally (already gitignored) and Secret Manager on Cloud Run. All Gemini calls happen server-side in Flask. The key must never appear in frontend code, logs, or commits.

## Stack

- Frontend: Vite vanilla JS SPA (no framework), Arabic RTL (`<html dir="rtl" lang="ar">`), self-hosted variable Readex Pro (woff2 unicode-range subsets in `frontend/public/fonts/`)
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

Browser MediaRecorder → `POST /api/rooms/{code}/turns` (multipart) → Flask saves to GCS → Gemini Flash transcribes the turn (verbatim Arabic, keep dialect as spoken) → transcript appended to room doc → both parties agree to finish → Gemini Pro judges full transcript → verdict JSON stored on room → both clients render verdict screen.

MediaRecorder notes: feature-detect mimeType (`audio/webm` Chrome/Android, `audio/mp4` iOS Safari) and send whichever exists. Recording must start from a user gesture (hold-to-record button). Cap turn length server-side too, not just in UI.

## Room state machine

`lobby → claims → turn_a1 → turn_b1 → turn_a2 → turn_b2 → deliberating → verdict`
Plus `abandoned` (no activity 30 min). Server is the source of truth for whose turn it is and timer deadlines; clients render, never decide.

## API

- `POST /api/rooms` `{topic}` → `{code, token}` (creator = debater A)
- `POST /api/rooms/{code}/join` `{name, claim, consent: true}` → `{token}` (debater B; consent checkbox required)
- `POST /api/rooms/{code}/claim` `{name, claim}` (A sets theirs)
- `POST /api/rooms/{code}/ready`
- `POST /api/rooms/{code}/turns` (multipart audio) — server validates it is this token's turn
- `GET /api/rooms/{code}` → full public state (poll target)
- `POST /api/rooms/{code}/finish` — flags agreement; judging starts only when both have flagged

Each debater gets a random token at create/join, sent as `X-Debater-Token` header. Rate-limit room creation per IP.

## Judging rules (Phase 2)

- Judge argumentation quality ONLY — not which side is factually true (tabula rasa judge)
- Anonymize before judging: strip names → المتحدث الأول / المتحدث الثاني
- temperature 0, Gemini structured output (`responseSchema`), Arabic output
- Position-bias control: run the judgment twice with speaker order swapped. Same winner → confident verdict. Winner flips → verdict is «متقاربة» (draw-ish), say so honestly
- Schema field order: all analysis fields first, `winner` and `margin` LAST (reason before deciding)
- Per-debater 0–100 scores on 5 axes: الاتساق المنطقي، الالتزام بالموضوع، الرد على النقاط، الوضوح، الهدوء والعقلانية (قوة الاستدلال folded into الاتساق المنطقي؛ النزاهة الحجاجية dropped as a scored axis — it surfaces only via `fallacies[]` below)
- `fallacies[]`: `{name_ar, name_en, quote, turn, severity, explanation_ar}` — quote must be verbatim from transcript
- Also: `dropped_points[]` (arguments never answered), `key_moment`, strongest + weakest point per debater, one improvement tip per debater, `confidence`, `reasoning_ar` (2 sentences max)
- Model IDs: Flash for transcription, Pro for judging — verify current model names against Gemini docs at build time, do not hardcode from memory

## Frontend screens (match /design exports exactly)

1. Landing — wordmark الحَكَم (definite article, matches thehakam.com; keep the fatḥas — distinct from الحُكْم "verdict"), tagline «لتكن الحُجّة هي الفيصل», topic input, create CTA, join-by-code link
2. Lobby — invite link + copy, two debater claim cards (teal/coral), format row, ready gating
3. Live debate — pinned claim chips, circular countdown, hold-to-record mic, live transcript feed, turn progress dots, mutual finish request
4. Verdict — hero verdict card (brass), radar chart both debaters (Chart.js), emotionality meters, fallacy receipt cards, نقاط بلا رد, اللحظة الفاصلة, tip cards, share + rematch

All UI text in Arabic. Western tabular numerals for timers/scores. RTL must be real (mirrored layout/icons), not just right-aligned text.

## Build phases — do not mix them

- **Phase 1 (no AI):** rooms, invite links, claims, ready-up, server-driven turn timers, hold-to-record + upload + playback, polling sync, mutual finish. Done = full debate between two phones, audio lands in GCS, every state transition correct.
- **Phase 2:** Flash transcription per turn (transcript appears live), Pro judge, full verdict screen, rematch.
- **Phase 3 (later, do not build now):** shareable verdict image, same-device pass-the-phone mode, Capacitor wrap, history.

## Workflow rules

- Plan first: before implementing any feature, present the plan (files, endpoints, data shapes) and wait for approval
- Root-cause over guessing when debugging
- Never commit: root `.env`, keys, service-account JSON, `node_modules`, `dist`, audio files. (`frontend/.env.{production,development}` hold only public backend URLs and ARE committed.)
- Deploy backend: `export CLOUDSDK_ACTIVE_CONFIG_NAME=hakam` then `gcloud run deploy hakam --source . --project hakam-501212 --region me-central1 --set-env-vars GOOGLE_CLOUD_PROJECT=hakam-501212,HAKAM_AUDIO_BUCKET=hakam-audio` (public; on redeploys omit `--allow-unauthenticated`, access is preserved). Frontend auto-deploys to GitHub Pages via the Actions workflow.
