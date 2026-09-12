import pytest

pytest.importorskip("jose")
from jose import JWTError

from masyg_extractor.config import jwt_config


def test_access_and_refresh_tokens_are_not_interchangeable(monkeypatch):
    monkeypatch.setattr(jwt_config, "SECRET_KEY", "unit-test-secret")
    monkeypatch.setattr(jwt_config, "ALGORITHM", "HS256")

    access = jwt_config.create_access_token({"sub": "user-1"})
    refresh = jwt_config.create_refresh_token(
        {"sub": "user-1"},
        session_id="session-1",
        jti="refresh-jti",
    )

    assert jwt_config.decode_jwt_token(access, expected_type="access")["sub"] == "user-1"
    refresh_payload = jwt_config.decode_jwt_token(refresh, expected_type="refresh")
    assert refresh_payload["session_id"] == "session-1"
    assert refresh_payload["jti"] == "refresh-jti"

    with pytest.raises(JWTError):
        jwt_config.decode_jwt_token(refresh, expected_type="access")
    with pytest.raises(JWTError):
        jwt_config.decode_jwt_token(access, expected_type="refresh")
