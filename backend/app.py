"""Flask entrypoint.

One Cloud Run service serves both the JSON API (/api/*) and the built Vite SPA
(everything else -> dist/index.html), so there is no CORS to configure.

Run locally:   HAKAM_LOCAL=1 flask --app backend.app run --port 8080
Production:    gunicorn -b :$PORT backend.app:app
"""
from __future__ import annotations

import mimetypes
import os

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.exceptions import RequestEntityTooLarge

from . import config
from .rooms import api

# Some slim base images don't know .woff2; register it so fonts serve as font/woff2.
mimetypes.add_type("font/woff2", ".woff2")


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

    # NOTE: Cloud Run's Google Front End reserves /healthz (it 404s before reaching
    # the container), so /health is the externally reachable health path. Both are
    # registered — /healthz still works locally and off-GFE.
    @app.get("/health")
    @app.get("/healthz")
    def health():
        return jsonify({"status": "ok", "mode": "local" if config.LOCAL_MODE else "cloud"})

    @app.errorhandler(RequestEntityTooLarge)
    def _too_large(_e):
        return jsonify({"error": "audio_too_large", "message": "التسجيل أكبر من المسموح."}), 413

    @app.errorhandler(404)
    def _not_found(_e):
        # Unknown API path -> JSON; anything else falls through to the SPA below.
        if request.path.startswith("/api/"):
            return jsonify({"error": "not_found", "message": "غير موجود."}), 404
        return _serve_spa("")

    # --- static SPA (built frontend) ---------------------------------------
    dist = str(config.DIST_DIR)

    def _serve_spa(path: str):
        index = config.DIST_DIR / "index.html"
        if not index.exists():
            return jsonify({
                "status": "no_build",
                "message": "Frontend not built. Run `npm --prefix frontend run build`, "
                           "or use the Vite dev server on :5173 in development.",
            }), 200
        target = config.DIST_DIR / path
        if path and target.is_file():
            return send_from_directory(dist, path)
        return send_from_directory(dist, "index.html")

    @app.get("/")
    def _root():
        return _serve_spa("")

    @app.get("/<path:path>")
    def _spa(path):
        if path.startswith("api/"):
            return jsonify({"error": "not_found", "message": "غير موجود."}), 404
        return _serve_spa(path)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=config.LOCAL_MODE)
