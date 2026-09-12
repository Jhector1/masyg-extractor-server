"""Canonical browser-origin configuration shared by HTTP CORS and Socket.IO."""
from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit


def _normalize_origin(value: str | None) -> str | None:
    origin = (value or "").strip().rstrip("/")
    if not origin:
        return None
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    if parsed.path not in {"", "/"}:
        return None
    try:
        _ = parsed.port
    except ValueError:
        return None
    # Browser Origin values never include path/query/fragment.
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), "", "", ""))


def _split_origins(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _loopback_alias(origin: str) -> str | None:
    """Return localhost <-> 127.0.0.1 alias with the same scheme/port."""
    parsed = urlsplit(origin)
    host = (parsed.hostname or "").lower()
    if host not in {"localhost", "127.0.0.1"}:
        return None
    alias_host = "127.0.0.1" if host == "localhost" else "localhost"
    netloc = alias_host if parsed.port is None else f"{alias_host}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, "", "", ""))


def build_allowed_origins(environment: str | None = None) -> list[str]:
    """Build a precise credential-safe origin allowlist from runtime configuration.

    Production only accepts explicitly configured origins. Development also adds the
    localhost/127.0.0.1 alias for each configured local origin so changing how the
    browser addresses the same dev server does not break credentialed preflights.
    """
    env = (environment or os.getenv("FAST_API_ENV", "development")).strip().lower()
    candidates: list[str] = []
    candidates.extend(_split_origins(os.getenv("CLIENT_URL")))
    if env != "production":
        candidates.extend(_split_origins(os.getenv("DEV_CLIENT_URL")))
    candidates.extend(_split_origins(os.getenv("CORS_EXTRA")))

    if env != "production" and not candidates:
        candidates.append("http://localhost:4000")

    result: list[str] = []
    seen: set[str] = set()

    def add(value: str | None) -> None:
        normalized = _normalize_origin(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)

    for candidate in candidates:
        add(candidate)
        if env != "production":
            normalized = _normalize_origin(candidate)
            if normalized:
                add(_loopback_alias(normalized))

    return result


ALLOWED_ORIGINS = build_allowed_origins()
