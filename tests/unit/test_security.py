from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from masyg_extractor.security import (
    cookie_security_options,
    generate_password_reset_token,
    hash_password_reset_token,
    normalize_email,
    reset_token_is_expired,
    stripe_session_belongs_to_user,
)


def test_normalize_email_trims_and_lowercases():
    assert normalize_email("  User@Example.COM ") == "user@example.com"


def test_reset_token_is_not_stored_as_plaintext():
    raw, digest, expires_at = generate_password_reset_token(ttl_minutes=30)
    assert raw
    assert digest == hash_password_reset_token(raw)
    assert raw != digest
    assert len(digest) == 64
    assert expires_at > datetime.now(timezone.utc)


def test_reset_expiration_rejects_missing_or_expired_values():
    now = datetime.now(timezone.utc)
    assert reset_token_is_expired(None, now=now)
    assert reset_token_is_expired(now - timedelta(seconds=1), now=now)
    assert not reset_token_is_expired(now + timedelta(seconds=1), now=now)


def test_naive_expiration_is_treated_as_utc():
    now = datetime.now(timezone.utc)
    future_naive = (now + timedelta(minutes=5)).replace(tzinfo=None)
    assert not reset_token_is_expired(future_naive, now=now)


def test_cookie_options_allow_local_http_but_secure_production():
    assert cookie_security_options("development") == {
        "secure": False,
        "samesite": "lax",
        "path": "/",
    }
    assert cookie_security_options("production") == {
        "secure": True,
        "samesite": "none",
        "path": "/",
    }


def test_stripe_session_owner_matches_client_reference():
    session = SimpleNamespace(client_reference_id="user-1", metadata={}, customer="cus-other")
    assert stripe_session_belongs_to_user(session, "user-1", "cus-1")


def test_stripe_session_owner_matches_metadata():
    session = SimpleNamespace(client_reference_id=None, metadata={"firebaseUserId": "user-1"}, customer=None)
    assert stripe_session_belongs_to_user(session, "user-1", None)


def test_stripe_session_owner_matches_known_customer():
    session = SimpleNamespace(client_reference_id=None, metadata={}, customer="cus-1")
    assert stripe_session_belongs_to_user(session, "user-1", "cus-1")


def test_stripe_session_owner_rejects_unrelated_session():
    session = SimpleNamespace(client_reference_id="user-2", metadata={"firebaseUserId": "user-2"}, customer="cus-2")
    assert not stripe_session_belongs_to_user(session, "user-1", "cus-1")
