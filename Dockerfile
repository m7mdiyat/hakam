# syntax=docker/dockerfile:1
# Stage 1 — build the Vite SPA.
FROM node:20-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# Stage 2 — Python runtime; Flask serves the API and the built dist/.
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY --from=frontend /app/frontend/dist frontend/dist
EXPOSE 8080
# HAKAM_LOCAL is intentionally unset here -> Firestore + GCS in production.
CMD exec gunicorn -b :$PORT -w 2 -k gthread --threads 8 --timeout 120 backend.app:app
