"""Audio blob storage.

GCS in production (bucket has a 2-day lifecycle rule), local disk under HAKAM_LOCAL=1.
Only the resulting URI is stored on the room doc; bytes never enter Firestore. Audio
is served back to participants by proxying through Flask (see rooms.py) so blobs stay
private and we avoid signed-URL service-account key handling on Cloud Run.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from . import config

_EXT = {
    "audio/webm": "webm",
    "audio/mp4": "m4a",
    "audio/ogg": "ogg",
    "audio/mpeg": "mp3",
    "audio/aac": "aac",
}


def ext_for(content_type: str) -> str:
    base = (content_type or "").split(";")[0].strip().lower()
    return _EXT.get(base, "bin")


def _basename(turn_key: str, content_type: str, variant: str = "") -> str:
    """`variant` keeps derived renditions from colliding with the original —
    an iOS original (audio/mp4) and its canonical m4a would otherwise share
    `turn_a1.m4a`. E.g. variant="norm" -> turn_a1.norm.m4a."""
    mid = f".{variant}" if variant else ""
    return f"{turn_key}{mid}.{ext_for(content_type)}"


class GcsStorage:
    def __init__(self):
        from google.cloud import storage
        self._client = storage.Client(project=config.PROJECT_ID)
        self._bucket = self._client.bucket(config.AUDIO_BUCKET)

    def save(self, code: str, turn_key: str, data: bytes, content_type: str,
             variant: str = "") -> str:
        blob_name = f"rooms/{code}/{_basename(turn_key, content_type, variant)}"
        blob = self._bucket.blob(blob_name)
        blob.upload_from_string(data, content_type=content_type)
        return f"gs://{config.AUDIO_BUCKET}/{blob_name}"

    def read(self, uri: str) -> bytes:
        parsed = urlparse(uri)  # gs://bucket/blob/path
        bucket = self._client.bucket(parsed.netloc)
        blob = bucket.blob(parsed.path.lstrip("/"))
        return blob.download_as_bytes()

    def copy_shared(self, uri: str, share_id: str) -> str:
        """Server-side copy of a room blob under shared/{id}/ — the shared
        prefix carries its own (longer) lifecycle rule, so share links
        outlive the 2-day room audio."""
        parsed = urlparse(uri)
        src_bucket = self._client.bucket(parsed.netloc)
        src = src_bucket.blob(parsed.path.lstrip("/"))
        dest_name = f"shared/{share_id}/{parsed.path.rsplit('/', 1)[-1]}"
        src_bucket.copy_blob(src, self._bucket, dest_name)
        return f"gs://{config.AUDIO_BUCKET}/{dest_name}"


class LocalStorage:
    def __init__(self):
        self.root = Path(config.LOCAL_AUDIO_DIR)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, code: str, turn_key: str, data: bytes, content_type: str,
             variant: str = "") -> str:
        rel = f"rooms/{code}/{_basename(turn_key, content_type, variant)}"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return f"local://{rel}"

    def read(self, uri: str) -> bytes:
        rel = uri[len("local://"):] if uri.startswith("local://") else uri
        with open(self.root / rel, "rb") as f:
            return f.read()

    def copy_shared(self, uri: str, share_id: str) -> str:
        rel = uri[len("local://"):] if uri.startswith("local://") else uri
        dest_rel = f"shared/{share_id}/{rel.rsplit('/', 1)[-1]}"
        dest = self.root / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(self.root / rel, "rb") as src, open(dest, "wb") as out:
            out.write(src.read())
        return f"local://{dest_rel}"


_storage = None


def get_storage():
    global _storage
    if _storage is None:
        _storage = LocalStorage() if config.LOCAL_MODE else GcsStorage()
    return _storage
