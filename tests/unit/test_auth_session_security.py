from datetime import datetime, timedelta, timezone

from masyg_extractor.security import hash_refresh_jti, refresh_session_matches


def test_refresh_session_stores_only_jti_hash_and_matches_current_token():
    jti = "refresh-token-jti"
    record = {
        "refreshJtiHash": hash_refresh_jti(jti),
        "expiresAt": datetime.now(timezone.utc) + timedelta(minutes=5),
    }
    assert record["refreshJtiHash"] != jti
    assert refresh_session_matches(record, jti)
    assert not refresh_session_matches(record, "replayed-or-other-jti")


def test_refresh_session_rejects_expired_or_malformed_records():
    now = datetime.now(timezone.utc)
    assert not refresh_session_matches({}, "jti", now=now)
    assert not refresh_session_matches(
        {"refreshJtiHash": hash_refresh_jti("jti"), "expiresAt": now - timedelta(seconds=1)},
        "jti",
        now=now,
    )
