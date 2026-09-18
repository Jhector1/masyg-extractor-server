from __future__ import annotations

import json
import os

from cryptography.fernet import Fernet, InvalidToken


STATE_TTL_SECONDS = 10 * 60
_STATE_PURPOSE = "google_drive_oauth"


class GoogleDriveStateError(ValueError):
    pass


def _cipher() -> Fernet:
    key = (os.getenv("ENCRYPTION_KEY") or "").strip()
    if not key:
        raise GoogleDriveStateError("ENCRYPTION_KEY is required for Google Drive OAuth state.")
    try:
        return Fernet(key.encode())
    except (TypeError, ValueError) as exc:
        raise GoogleDriveStateError("ENCRYPTION_KEY is not a valid Fernet key.") from exc


def issue_google_drive_state(user_id: str) -> str:
    normalized = str(user_id or "").strip()
    if not normalized:
        raise GoogleDriveStateError("user_id is required")
    payload = json.dumps(
        {"purpose": _STATE_PURPOSE, "userId": normalized},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return _cipher().encrypt(payload).decode()


def read_google_drive_state(state: str) -> str:
    if not state:
        raise GoogleDriveStateError("OAuth state is required")
    try:
        raw = _cipher().decrypt(state.encode(), ttl=STATE_TTL_SECONDS)
        payload = json.loads(raw.decode())
    except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GoogleDriveStateError("OAuth state is invalid or expired") from exc

    if payload.get("purpose") != _STATE_PURPOSE:
        raise GoogleDriveStateError("OAuth state purpose is invalid")

    user_id = str(payload.get("userId") or "").strip()
    if not user_id:
        raise GoogleDriveStateError("OAuth state user is missing")
    return user_id
