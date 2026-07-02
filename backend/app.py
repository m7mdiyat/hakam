"""Flask entrypoint — API-only backend on Cloud Run.

The frontend lives on GitHub Pages (thehakam.com); this service exposes the JSON
API (/api/*) and a health check, and 302-redirects every other path to the
frontend (preserving path + query so old Cloud-Run links map over).

Run locally:   HAKAM_LOCAL=1 flask --app backend.app run --port 8080
Production:    gunicorn -b :$PORT backend.app:app
"""
from __future__ import annotations

import os

from flask import Flask, jsonify, redirect, request
from flask_cors import CORS
from werkzeug.exceptions import RequestEntityTooLarge

from . import config
from .rooms import api


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    # Independent server-side cap on upload size (UI soft-stop is not trusted).
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_AUDIO_BYTES + 1024 * 1024
    app.register_blueprint(api)

    # CORS: allow only the configured origins (GitHub Pages frontend + local dev),
    # scoped to /api/* (the only cross-origin surface). flask-cors reflects the
    # matching origin (never "*") and answers preflight incl. the X-Debater-Token
    # header. No cookies are used, so supports_credentials stays False.
    CORS(
        app,
        resources={r"/api/*": {"origins": config.CORS_ORIGINS}},
        allow_headers=["Content-Type", "X-Debater-Token"],
        methods=["GET", "POST", "OPTIONS"],
        supports_credentials=False,
        max_age=600,
    )

    # Health on /health (Cloud Run's Google Front End reserves /healthz and 404s it
    # before it reaches the container); both are registered so /healthz works locally.
    @app.get("/health")
    @app.get("/healthz")
    def health():
        return jsonify({"status": "ok", "mode": "local" if config.LOCAL_MODE else "cloud"})

    @app.errorhandler(RequestEntityTooLarge)
    def _too_large(_e):
        return jsonify({"error": "audio_too_large", "message": "التسجيل أكبر من المسموح."}), 413

    @app.errorhandler(404)
    def _not_found(_e):
        if request.path.startswith("/api/"):
            return jsonify({"error": "not_found", "message": "غير موجود."}), 404
        return redirect(config.FRONTEND_URL, code=302)

    # Everything that isn't the API belongs to the frontend on GitHub Pages.
    @app.get("/")
    def _root():
        return redirect(config.FRONTEND_URL + "/", code=302)

    @app.get("/<path:path>")
    def _frontend_redirect(path):
        if path.startswith("api/"):
            return jsonify({"error": "not_found", "message": "غير موجود."}), 404
        qs = f"?{request.query_string.decode()}" if request.query_string else ""
        # path is captured without a leading slash, so the target stays on FRONTEND_URL.
        return redirect(f"{config.FRONTEND_URL}/{path}{qs}", code=302)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=config.LOCAL_MODE)
