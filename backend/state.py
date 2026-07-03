"""Room domain model + state machine.

Pure functions over a plain room dict. The server is the single source of truth for
whose turn it is and for timer deadlines; clients only render. Time fields are
timezone-aware UTC datetimes in-memory; the store layer persists them and the API
layer serializes them to ISO-8601.

State machine:
    lobby -> claims -> turn_a1 -> turn_b1 -> turn_a2 -> turn_b2 -> deliberating
    plus: abandoned (no activity for ABANDON_MINUTES)
`state` equals the current turn key while a turn is active, so it doubles as the
"whose turn" pointer alongside turn_index (state == turn_order[turn_index]).
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

from . import config

# --- lifecycle states -------------------------------------------------------
LOBBY = "lobby"          # created; waiting for debater B to join
CLAIMS = "claims"        # both joined; setting claims + readying up
DELIBERATING = "deliberating"  # all turns done or both finished -> Phase 2 judges here
ABANDONED = "abandoned"
PRE_DEBATE = (LOBBY, CLAIMS)
TERMINAL = (DELIBERATING, ABANDONED)

SIDES = ("a", "b")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def build_turn_order(rounds_per_side: int) -> list[str]:
    """[a1, b1, a2, b2, ...] — A opens each round, then B."""
    order: list[str] = []
    for r in range(1, rounds_per_side + 1):
        order.append(f"turn_a{r}")
        order.append(f"turn_b{r}")
    return order


def side_of_turn(turn_key: str) -> str:
    # "turn_a1" -> "a"
    return turn_key.split("_")[1][0]


def is_turn_state(state: str) -> bool:
    return state.startswith("turn_")


# --- construction -----------------------------------------------------------
def new_room(code: str, topic: str, token_a: str) -> dict:
    now = now_utc()
    order = build_turn_order(config.ROUNDS_PER_SIDE)
    return {
        "code": code,
        "topic": (topic or "").strip(),
        "state": LOBBY,
        "format": {
            "rounds_per_side": config.ROUNDS_PER_SIDE,
            "turn_seconds": config.TURN_SECONDS,
        },
        "debaters": {
            # A is the creator; consent is implicit by creating a recorded session.
            "a": {"name": None, "claim": None, "ready": False, "consent": True,
                  "joined_at": now},
            "b": {"name": None, "claim": None, "ready": False, "consent": False,
                  "joined_at": None},
        },
        "turn_order": order,
        "turn_index": 0,
        "turn_deadline_at": None,
        "turns": [],
        "finish_requested": {"a": False, "b": False},
        "verdict": None,  # Phase 2
        "secret_tokens": {"a": token_a, "b": None},
        "created_at": now,
        "updated_at": now,
        "last_activity_at": now,
        "expires_at": now + timedelta(hours=config.ROOM_TTL_HOURS),
    }


# --- helpers ----------------------------------------------------------------
def side_of_token(room: dict, token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    for s in SIDES:
        if room["secret_tokens"].get(s) == token:
            return s
    return None


def current_turn(room: dict) -> Optional[str]:
    if is_turn_state(room["state"]):
        return room["turn_order"][room["turn_index"]]
    return None


def has_submission(room: dict, turn_key: str) -> bool:
    return any(t["turn"] == turn_key for t in room["turns"])


def both_joined(room: dict) -> bool:
    return bool(room["debaters"]["a"]["name"]) and bool(room["debaters"]["b"]["name"])


def claim_set(room: dict, side: str) -> bool:
    d = room["debaters"][side]
    return bool(d["name"]) and bool(d["claim"])


def both_ready(room: dict) -> bool:
    return (
        claim_set(room, "a") and claim_set(room, "b")
        and room["debaters"]["a"]["ready"] and room["debaters"]["b"]["ready"]
    )


def _touch(room: dict, now: datetime, activity: bool = False) -> None:
    room["updated_at"] = now
    if activity:
        room["last_activity_at"] = now


def is_expired(room: dict, now: Optional[datetime] = None) -> bool:
    now = now or now_utc()
    return now > room["expires_at"]


# --- transitions (each returns True if it mutated) --------------------------
def start_debate(room: dict, now: Optional[datetime] = None) -> None:
    now = now or now_utc()
    room["state"] = room["turn_order"][0]
    room["turn_index"] = 0
    room["turn_deadline_at"] = now + timedelta(seconds=room["format"]["turn_seconds"])
    _touch(room, now, activity=True)


def advance_turn(room: dict, now: Optional[datetime] = None) -> None:
    now = now or now_utc()
    room["turn_index"] += 1
    if room["turn_index"] >= len(room["turn_order"]):
        room["state"] = DELIBERATING
        room["turn_deadline_at"] = None
    else:
        room["state"] = room["turn_order"][room["turn_index"]]
        room["turn_deadline_at"] = now + timedelta(seconds=room["format"]["turn_seconds"])
    _touch(room, now)


def record_turn(room: dict, side: str, audio_uri: str, duration_ms: int,
                content_type: str, m4a_uri: Optional[str] = None,
                duration_s: Optional[float] = None,
                transcribe_pending: bool = False,
                now: Optional[datetime] = None) -> None:
    """Append a real (recorded) turn for `side` and advance. Caller has validated turn.

    `m4a_uri`/`duration_s` come from the upload-time ffmpeg transcode: the canonical
    mono-AAC rendition (what clients play and Gemini reads) and its authoritative
    duration. None only when ffmpeg is unavailable (bare local dev).
    `transcribe_pending` marks the turn as awaiting the transcription worker
    (the caller enqueues it after this update commits)."""
    now = now or now_utc()
    turn_key = current_turn(room)
    room["turns"].append({
        "turn": turn_key,
        "debater": side,
        "audio_uri": audio_uri,
        "audio_m4a_uri": m4a_uri,
        "content_type": content_type,
        "duration_ms": int(duration_ms),
        "duration_s": duration_s,
        "forfeited": False,
        "transcript": {"status": "pending", "segments": [], "attempts": 0}
        if transcribe_pending else None,
        "created_at": now,
    })
    _touch(room, now, activity=True)
    advance_turn(room, now)


def _forfeit_current_turn(room: dict, now: datetime) -> None:
    """Auto-advance a turn whose deadline passed with no recording (no-show)."""
    turn_key = current_turn(room)
    room["turns"].append({
        "turn": turn_key,
        "debater": side_of_turn(turn_key),
        "audio_uri": None,
        "audio_m4a_uri": None,
        "duration_ms": 0,
        "duration_s": None,
        "forfeited": True,
        "transcript": None,
        "created_at": now,
    })
    # NOTE: forfeit is a system action -> does NOT bump last_activity_at, so an
    # abandoned room still eventually flips to ABANDONED.
    advance_turn(room, now)


def request_finish(room: dict, side: str, now: Optional[datetime] = None) -> None:
    now = now or now_utc()
    room["finish_requested"][side] = True
    _touch(room, now, activity=True)
    if room["finish_requested"]["a"] and room["finish_requested"]["b"]:
        room["state"] = DELIBERATING
        room["turn_deadline_at"] = None


# --- reconcile: the lazy, server-authoritative timer ------------------------
def reconcile(room: dict, now: Optional[datetime] = None) -> bool:
    """Apply time-driven transitions. Called on every read and mutation.

    - No-show: an active turn past deadline + NOSHOW_GRACE with no upload is
      forfeited and advanced (looped, to catch several missed turns at once).
    - Abandonment: a non-terminal room idle for ABANDON_MINUTES -> abandoned.

    Returns True if the room changed (so the store knows to persist).
    """
    now = now or now_utc()
    changed = False

    # No-show forfeits (bounded loop).
    for _ in range(len(room["turn_order"]) + 1):
        if not is_turn_state(room["state"]):
            break
        deadline = room["turn_deadline_at"]
        if not deadline:
            break
        overdue = now > deadline + timedelta(seconds=config.NOSHOW_GRACE_SECONDS)
        if overdue and not has_submission(room, current_turn(room)):
            _forfeit_current_turn(room, now)
            changed = True
        else:
            break

    # Abandonment (pre-debate idling or a fully-stalled live debate).
    if room["state"] in PRE_DEBATE or is_turn_state(room["state"]):
        idle = now - room["last_activity_at"]
        if idle > timedelta(minutes=config.ABANDON_MINUTES):
            room["state"] = ABANDONED
            room["turn_deadline_at"] = None
            changed = True

    if changed:
        room["updated_at"] = now
    return changed


# --- public projection ------------------------------------------------------
def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def public_view(room: dict, now: Optional[datetime] = None) -> dict:
    """Whitelisted, secret-free JSON view. Includes server_now for clock-skew-safe
    countdowns: the client renders remaining = turn_deadline_at - server_now."""
    now = now or now_utc()

    def debater(side: str) -> dict:
        d = room["debaters"][side]
        return {
            "side": side,
            "name": d["name"],
            "claim": d["claim"],
            "ready": d["ready"],
            "consent": d["consent"],
            "joined": bool(d["name"]),
        }

    turns = [{
        "turn": t["turn"],
        "debater": t["debater"],
        "duration_ms": t.get("duration_ms", 0),
        "duration_s": t.get("duration_s"),
        "forfeited": t.get("forfeited", False),
        "has_audio": bool(t.get("audio_uri")),
        "transcript": t.get("transcript"),  # null in Phase 1
        "created_at": _iso(t.get("created_at")),
    } for t in room["turns"]]

    return {
        "code": room["code"],
        "topic": room["topic"],
        "state": room["state"],
        "format": room["format"],
        "debaters": {"a": debater("a"), "b": debater("b")},
        "turn_order": room["turn_order"],
        "turn_index": room["turn_index"],
        "current_turn": current_turn(room),
        "turn_deadline_at": _iso(room["turn_deadline_at"]),
        "turns": turns,
        "finish_requested": room["finish_requested"],
        "both_ready": both_ready(room),
        "verdict": room["verdict"],
        "expires_at": _iso(room["expires_at"]),
        "server_now": _iso(now),
    }
