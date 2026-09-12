"""Small security primitives shared by HTTP routes.

Keep these functions framework-free so their behavior can be regression-tested without
Firebase, Stripe, or application startup side effects.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any


def normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


def generate_password_reset_token(*, ttl_minutes: int = 30) -> tuple[str, str, datetime]:
    raw_token = secrets.token_urlsafe(32)
    return raw_token, hash_password_reset_token(raw_token), datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)


def hash_password_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def reset_token_is_expired(expires_at: Any, *, now: datetime | None = None) -> bool:
    if not isinstance(expires_at, datetime):
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return expires_at <= current



def hash_refresh_jti(jti: str) -> str:
    return hashlib.sha256(jti.encode("utf-8")).hexdigest()


def refresh_session_matches(record: dict[str, Any], presented_jti: str, *, now: datetime | None = None) -> bool:
    stored_hash = str((record or {}).get("refreshJtiHash") or "")
    if not stored_hash or not secrets.compare_digest(stored_hash, hash_refresh_jti(presented_jti)):
        return False

    expires_at = (record or {}).get("expiresAt")
    if not isinstance(expires_at, datetime):
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return expires_at > current

def cookie_security_options(environment: str | None) -> dict[str, Any]:
    production = (environment or "development").strip().lower() == "production"
    return {
        "secure": production,
        "samesite": "none" if production else "lax",
        "path": "/",
    }


def stripe_session_belongs_to_user(session: Any, user_id: str, stripe_customer_id: str | None) -> bool:
    """Validate Stripe Checkout ownership using only server-issued identity fields."""
    client_reference_id = getattr(session, "client_reference_id", None)
    metadata = getattr(session, "metadata", None) or {}
    metadata_user_id = metadata.get("firebaseUserId") if hasattr(metadata, "get") else None
    session_customer = getattr(session, "customer", None)
    return bool(
        client_reference_id == user_id
        or metadata_user_id == user_id
        or (stripe_customer_id and session_customer == stripe_customer_id)
    )
