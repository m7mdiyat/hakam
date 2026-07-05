"""Room codes and debater tokens.

Room codes: 6 chars from an unambiguous alphabet (no 0/O/1/I) so they can be read
aloud / typed on a phone. Tokens: opaque bearer capabilities, never shown publicly.
"""
import secrets

# 31 unambiguous chars: A-Z minus I,O  +  digits 2-9 (no 0/1).
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6


def gen_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def gen_token() -> str:
    # URL-safe, ~32 bytes of entropy; room-scoped, expires with the room.
    return secrets.token_urlsafe(24)


SHARE_ID_LENGTH = 16


def gen_share_id() -> str:
    """Public share-link id: unguessable (31^16 ≈ 10^24) yet the same friendly
    alphabet as room codes — the link IS the access control."""
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(SHARE_ID_LENGTH))


_ALPHABET_SET = set(CODE_ALPHABET)


def normalize_code(raw: str) -> str:
    """Uppercase a user-typed code and keep only valid alphabet chars.

    Codes never contain the excluded lookalikes (0/O/1/I/L), so we simply drop any
    character that isn't in the alphabet (spaces, dashes, stray lookalikes).
    """
    if not raw:
        return ""
    return "".join(ch for ch in raw.strip().upper() if ch in _ALPHABET_SET)
