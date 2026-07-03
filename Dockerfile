# syntax=docker/dockerfile:1
# API-only backend. The frontend deploys separately to GitHub Pages (thehakam.com);
# Cloud Run redirects any non-API path there.
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080
WORKDIR /app
# ffmpeg: every uploaded turn is transcoded to a canonical m4a (backend/audio.py).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
EXPOSE 8080
# HAKAM_LOCAL is intentionally unset here -> Firestore + GCS in production.
CMD exec gunicorn -b :$PORT -w 2 -k gthread --threads 8 --timeout 120 backend.app:app
