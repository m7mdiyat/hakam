# CLAUDE.md — Hakam (حَكَم)

Arabic AI debate judge. Two people join a room via invite link, each states a claim, they debate in timed recorded voice turns, and Gemini judges the arguments — logic, fallacies, emotionality — delivering a structured verdict (الحُكْم).

## Environment rules (critical — read first)

- Before ANY `gcloud` command in any session, run: `export CLOUDSDK_ACTIVE_CONFIG_NAME=hakam`
- GCP project: `hakam-501212` · region: `me-central1` · bucket: `gs://hakam-audio`
- NEVER touch the default gcloud config or anything related to m7mdiyat / `handy-digit-482820-m6`
- `GEMINI_API_KEY` comes from `.env` locally (already gitignored) and Secret Manager on Cloud Run. All Gemini calls happen server-side in Flask. The key must never appear in frontend code, logs, or commits.

## Stack

- Frontend: Vite vanilla JS SPA (no framework), Arabic RTL (`<html dir="rtl" lang="ar">`), Readex Pro via Google Fonts
- Visual spec: `/design/*.png` — dark ink background, brass/gold accent (verdict + primary CTAs only), Debater A = teal, Debater B = coral, everywhere and consistently
- Backend: Python Flask on Cloud Run
- Room state: Firestore (native mode), `rooms` collection — never in-memory (Cloud Run instances restart/scale)
- Audio: GCS `hakam-audio`, lifecycle rule deletes objects after 2 days. Audio never stored in repo or Firestore
- Realtime sync: frontend polls `GET /api/rooms/{code}` every 2s. No WebSockets in MVP
- Serving: Flask serves the built `dist/` as static files → one Cloud Run service, one URL, no CORS setup
- No user accounts. First names only. Room codes: 6 chars (unambiguous set, no 0/O/1/I), expire after 24h

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
- Per-debater 0–100 scores on 7 axes: الاتساق المنطقي، قوة الاستدلال، الصلة بالموضوع، جودة الرد، العقلانية، الوضوح، النزاهة الحجاجية
- `fallacies[]`: `{name_ar, name_en, quote, turn, severity, explanation_ar}` — quote must be verbatim from transcript
- Also: `dropped_points[]` (arguments never answered), `key_moment`, strongest + weakest point per debater, one improvement tip per debater, `confidence`, `reasoning_ar` (2 sentences max)
- Model IDs: Flash for transcription, Pro for judging — verify current model names against Gemini docs at build time, do not hardcode from memory

## Frontend screens (match /design exports exactly)

1. Landing — wordmark حَكَم, tagline «لتكن الحُجّة هي الفيصل», topic input, create CTA, join-by-code link
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
- Never commit: `.env`, keys, service-account JSON, `node_modules`, `dist`, audio files
- Deploy: `gcloud run deploy hakam --source . --region me-central1` (exact flags decided at first deploy)
