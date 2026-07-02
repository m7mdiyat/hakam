"""Room persistence.

Two interchangeable backends behind one tiny interface:
  - FirestoreStore  : production (Cloud Run) and the Firestore emulator.
  - LocalStore      : HAKAM_LOCAL=1 dev — file-backed JSON with a cross-process
                      lock, so the full Phase-1 flow runs with no GCP credentials.

The interface is deliberately minimal:
  create(room)                       -> None            (raises AlreadyExists)
  get(code)                          -> room | None
  update(code, mutator)              -> room            (atomic read-modify-write)
  get_reconciled(code)               -> room | None     (read + apply lazy timers)
  rate_check(key, limit, window_s)   -> bool            (durable fixed-window)

Room dicts hold timezone-aware datetimes in-memory; each backend handles its own
persistence conversion.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from . import config
from . import state as S


class StoreError(Exception):
    pass


class NotFound(StoreError):
    pass


class AlreadyExists(StoreError):
    pass


# ---------------------------------------------------------------------------
# Firestore backend
# ---------------------------------------------------------------------------
class FirestoreStore:
    COLLECTION = "rooms"
    RATE_COLLECTION = "ratelimits"

    def __init__(self):
        from google.cloud import firestore  # imported lazily so LocalStore needs no GCP libs
        self._firestore = firestore
        kwargs = {"project": config.PROJECT_ID}
        if config.FIRESTORE_DATABASE and config.FIRESTORE_DATABASE != "(default)":
            kwargs["database"] = config.FIRESTORE_DATABASE
        self.db = firestore.Client(**kwargs)

    def _ref(self, code: str):
        return self.db.collection(self.COLLECTION).document(code)

    def create(self, room: dict) -> None:
        from google.api_core import exceptions as gexc
        try:
            self._ref(room["code"]).create(room)
        except gexc.AlreadyExists as e:
            raise AlreadyExists(room["code"]) from e

    def get(self, code: str) -> Optional[dict]:
        snap = self._ref(code).get()
        return snap.to_dict() if snap.exists else None

    def update(self, code: str, mutator: Callable[[dict], None]) -> dict:
        firestore = self._firestore
        ref = self._ref(code)

        @firestore.transactional
        def txn(transaction):
            snap = ref.get(transaction=transaction)
            if not snap.exists:
                raise NotFound(code)
            room = snap.to_dict()
            mutator(room)  # may raise to abort (no commit)
            transaction.set(ref, room)
            return room

        return txn(self.db.transaction())

    def get_reconciled(self, code: str) -> Optional[dict]:
        room = self.get(code)
        if room is None:
            return None
        if S.reconcile(room, S.now_utc()):
            # Persist authoritatively under a transaction (re-reconcile on fresh data).
            room = self.update(code, lambda r: S.reconcile(r, S.now_utc()))
        return room

    def rate_check(self, key: str, limit: int, window_s: int) -> bool:
        firestore = self._firestore
        ref = self.db.collection(self.RATE_COLLECTION).document(key)
        now = S.now_utc()

        @firestore.transactional
        def txn(transaction) -> bool:
            snap = ref.get(transaction=transaction)
            data = snap.to_dict() if snap.exists else None
            if not data or (now - data["window_start"]).total_seconds() > window_s:
                transaction.set(ref, {"window_start": now, "count": 1, "expires_at": now})
                return True
            if data["count"] >= limit:
                return False
            transaction.update(ref, {"count": data["count"] + 1})
            return True

        return txn(self.db.transaction())


# ---------------------------------------------------------------------------
# Local (file-backed) backend — dev only
# ---------------------------------------------------------------------------
class _DTEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, datetime):
            return {"__dt__": o.astimezone(timezone.utc).isoformat()}
        return super().default(o)


def _dt_hook(d: dict):
    if "__dt__" in d:
        return datetime.fromisoformat(d["__dt__"])
    return d


class LocalStore:
    """Single global lock serializes all ops. Fine for 2 debaters at low rate.

    Uses fcntl.flock across processes when available (macOS/Linux), plus an
    in-process threading lock for the dev server's worker threads.
    """

    def __init__(self):
        self.dir = Path(config.LOCAL_STORE_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.dir / ".lock"
        self.lock_path.touch(exist_ok=True)
        self._tlock = threading.Lock()

    def _room_path(self, code: str) -> Path:
        return self.dir / f"room_{code}.json"

    def _rate_path(self, key: str) -> Path:
        safe = "".join(c if c.isalnum() else "_" for c in key)
        return self.dir / f"rate_{safe}.json"

    class _FileLock:
        def __init__(self, outer):
            self.outer = outer
            self.fh = None

        def __enter__(self):
            self.outer._tlock.acquire()
            self.fh = open(self.outer.lock_path, "w")
            try:
                import fcntl
                fcntl.flock(self.fh, fcntl.LOCK_EX)
            except Exception:
                pass  # fcntl unavailable (e.g. Windows) — thread lock still holds
            return self

        def __exit__(self, *exc):
            try:
                import fcntl
                fcntl.flock(self.fh, fcntl.LOCK_UN)
            except Exception:
                pass
            self.fh.close()
            self.outer._tlock.release()

    def _read(self, path: Path) -> Optional[dict]:
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f, object_hook=_dt_hook)

    def _write(self, path: Path, data: dict) -> None:
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, cls=_DTEncoder, ensure_ascii=False)
        os.replace(tmp, path)  # atomic swap

    def create(self, room: dict) -> None:
        with self._FileLock(self):
            path = self._room_path(room["code"])
            if path.exists():
                raise AlreadyExists(room["code"])
            self._write(path, room)

    def get(self, code: str) -> Optional[dict]:
        with self._FileLock(self):
            return self._read(self._room_path(code))

    def update(self, code: str, mutator: Callable[[dict], None]) -> dict:
        with self._FileLock(self):
            path = self._room_path(code)
            room = self._read(path)
            if room is None:
                raise NotFound(code)
            mutator(room)  # may raise to abort (nothing written)
            self._write(path, room)
            return room

    def get_reconciled(self, code: str) -> Optional[dict]:
        with self._FileLock(self):
            path = self._room_path(code)
            room = self._read(path)
            if room is None:
                return None
            if S.reconcile(room, S.now_utc()):
                self._write(path, room)
            return room

    def rate_check(self, key: str, limit: int, window_s: int) -> bool:
        with self._FileLock(self):
            path = self._rate_path(key)
            now = S.now_utc()
            data = self._read(path)
            if not data or (now - data["window_start"]).total_seconds() > window_s:
                self._write(path, {"window_start": now, "count": 1})
                return True
            if data["count"] >= limit:
                return False
            data["count"] += 1
            self._write(path, data)
            return True


# ---------------------------------------------------------------------------
# Singleton selector
# ---------------------------------------------------------------------------
_store = None


def get_store():
    global _store
    if _store is None:
        _store = LocalStore() if config.LOCAL_MODE else FirestoreStore()
    return _store
