# حَكَم — Hakam

Arabic AI debate judge. Two people join a room via an invite link, each states a
claim, they debate in timed recorded voice turns, and (Phase 2) Gemini judges the
arguments and delivers a structured verdict — الحُكْم.

> **Status: Phase 1 complete** — rooms, invite links, claims, ready-up,
> server-authoritative turn timers, hold-to-record + upload + playback, 2s polling
> sync, and mutual finish. No AI yet (transcription + judging land in Phase 2).

See `CLAUDE.md` for the full product spec and `design/NOTES.md` for the extracted
design tokens (the visual source of truth is `design/hakam-design.html`).

## Architecture

- **Frontend** — Vite vanilla-JS SPA, Arabic RTL, self-hosted variable Readex Pro.
  Served by Flask as a single origin, **or** hosted on GitHub Pages (`thehakam.com`)
  calling the Cloud Run API cross-origin (see Split hosting below).
- **Backend** — Python Flask. Room **state** lives in Firestore (never in-memory);
  **audio** lives in GCS (`hakam-audio`, 2-day lifecycle). All state transitions run
  in transactions so the server stays authoritative across scaled Cloud Run instances.
- **Server-authoritative timer** — a lazy deadline in Firestore. `GET /api/rooms/{code}`
  returns `turn_deadline_at` **and** `server_now`; the client renders
  `remaining = deadline − server_now` and re-syncs every poll, so device clock skew
  can't cheat the clock. Turn uploads are re-validated (right turn, within deadline+grace)
  inside the atomic update; overdue turns auto-forfeit on the next poll.

```
backend/    Flask app, Firestore/GCS stores, state machine, API
frontend/   Vite SPA (screens, recorder, polling), self-hosted fonts
design/     hakam-design.html (source of truth) + NOTES.md (tokens)
Dockerfile  multi-stage: node builds the SPA → Python serves it
```

## Split hosting

The **frontend** is hosted on **GitHub Pages** (`thehakam.com`); the **backend** is
API-only on **Cloud Run** and 302-redirects every non-API path to the frontend.

- `frontend/src/api.js` targets `VITE_API_BASE_URL` — `frontend/.env.production` → Cloud
  Run URL, `frontend/.env.development` → `http://localhost:8080`.
- The backend enables **CORS on `/api/*`**, restricted to `HAKAM_CORS_ORIGINS` (default
  `https://thehakam.com,http://localhost:5173`) — the matching origin is reflected, never `*`.
- **SPA routing on Pages**: `frontend/public/404.html` + a decode snippet in `index.html`
  restore deep links like `/j/CODE` (Pages has no server-side routing).

See **Frontend deploy (GitHub Pages)** below for the workflow + DNS.

## Local development

No GCP credentials needed — `HAKAM_LOCAL=1` uses a file-backed store + local-disk audio.
The backend is API-only, so run the SPA with Vite:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt
npm --prefix frontend install

# terminal 1 — API on :8080
HAKAM_LOCAL=1 flask --app backend.app run --port 8080
# terminal 2 — UI on :5173 (calls :8080 cross-origin; CORS allows localhost:5173)
npm --prefix frontend run dev
# open http://localhost:5173
```

To exercise the two-debater flow locally, open the room as A, copy the invite link,
and open it in a second browser/profile as B.

## Environment

Copy `.env.example` to `.env` (gitignored). Key vars:

| Var | Purpose |
|---|---|
| `HAKAM_LOCAL=1` | file-backed store + local audio (dev). **Unset in prod** → Firestore + GCS. |
| `GOOGLE_CLOUD_PROJECT` | `hakam-501212` |
| `HAKAM_AUDIO_BUCKET` | `hakam-audio` |
| `HAKAM_TURN_SECONDS` | seconds per turn (default 120) |
| `HAKAM_ROUNDS_PER_SIDE` | rounds each debater gets (default 2) |
| `HAKAM_CORS_ORIGINS` | CORS allow-list for `/api/*` (default `https://thehakam.com,http://localhost:5173`) |
| `HAKAM_FRONTEND_URL` | where the Cloud Run root redirects (default `https://thehakam.com`) |
| `VITE_API_BASE_URL` | **frontend** build var — backend base URL (`frontend/.env.{production,development}`) |
| `GEMINI_API_KEY` | Phase 2 only — from Secret Manager on Cloud Run, `.env` locally. Never sent to the frontend. |

## Deploy (Cloud Run)

```bash
export CLOUDSDK_ACTIVE_CONFIG_NAME=hakam        # required before any gcloud
gcloud run deploy hakam --source . --region me-central1
```

The runtime service account needs Firestore + GCS access (and, in Phase 2, Secret
Manager). `HAKAM_LOCAL` must be unset in production so Firestore/GCS are used. The
service is API-only and redirects the root to `thehakam.com` (`HAKAM_FRONTEND_URL`).

## Frontend deploy (GitHub Pages)

`.github/workflows/deploy-pages.yml` builds `frontend/` and publishes `dist/` to Pages
on every push to `main` touching `frontend/**`. The build bakes in the Cloud Run URL
(`frontend/.env.production`) and ships `CNAME` (`thehakam.com`) + `404.html`.

One-time setup:

1. **DNS** for `thehakam.com` at your registrar — apex records to GitHub Pages:
   - **A**: `185.199.108.153` · `185.199.109.153` · `185.199.110.153` · `185.199.111.153`
   - **AAAA**: `2606:50c0:8000::153` · `2606:50c0:8001::153` · `2606:50c0:8002::153` · `2606:50c0:8003::153`
2. **GitHub → Settings → Pages** → Source: **GitHub Actions**. The custom domain comes
   from the `CNAME` file; turn on **Enforce HTTPS** once the certificate provisions.

## Tests

`python scripts/smoke.py` (or the equivalent) drives the full backend flow in LOCAL
mode: create → join → claim → ready → start → 4 turns → deliberating, plus auth/turn
guards, audio proxy, rate limiting, no-show forfeit, and abandonment.
