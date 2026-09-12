from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
import os
import secrets
import uuid

from fastapi import Header, HTTPException, Request, status
from jose import JWTError, jwt

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM") or "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "2"))


def _create_token(
    data: dict,
    *,
    token_type: str,
    expires_delta: timedelta,
    jti: str | None = None,
    extra_claims: dict | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload = data.copy()
    payload.update({
        "iat": now,
        "exp": now + expires_delta,
        "jti": jti or str(uuid.uuid4()),
        "token_type": token_type,
    })
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    return _create_token(
        data,
        token_type="access",
        expires_delta=expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def create_refresh_token(
    data: dict,
    *,
    session_id: str,
    remember_me: bool = False,
    jti: str | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    return _create_token(
        data,
        token_type="refresh",
        expires_delta=expires_delta or timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        jti=jti,
        extra_claims={"session_id": session_id, "remember_me": bool(remember_me)},
    )


def decode_jwt_token(token: str, *, expected_type: str | None = None) -> dict:
    payload = jwt.decode(
        token,
        SECRET_KEY,
        algorithms=[ALGORITHM],
        options={"verify_signature": True, "verify_exp": True, "verify_nbf": False},
    )
    if expected_type and payload.get("token_type") != expected_type:
        raise JWTError(f"Unexpected token type: {payload.get('token_type')!r}")
    return payload


def get_current_user_from_cookie(
    request: Request,
    csrf_token_header: Optional[str] = Header(None, alias="X-CSRF-Token"),
):
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing access token cookie",
        )

    try:
        payload = decode_jwt_token(token, expected_type="access")
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )

        # Safe/idempotent reads do not require CSRF validation.
        if request.method == "GET":
            return {
                "userId": user_id,
                "username": payload.get("username"),
                "email": payload.get("email"),
            }

        csrf_cookie = request.cookies.get("csrf_token")
        if not csrf_cookie:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing CSRF token cookie",
            )
        if not csrf_token_header or not secrets.compare_digest(csrf_token_header, csrf_cookie):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF token mismatch",
            )

        return {
            "userId": user_id,
            "username": payload.get("username"),
            "email": payload.get("email"),
        }
    except HTTPException:
        raise
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )


def verify_jwt_token(token: str, expected_type: str | None = None) -> dict:
    try:
        return decode_jwt_token(token, expected_type=expected_type)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def generate_csrf_token() -> str:
    return secrets.token_hex(16)
