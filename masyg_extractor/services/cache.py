"""Best-effort cache helpers.

Redis is an accelerator for Masyg, not a source of truth. Cache outages must not
turn otherwise healthy API requests into 5xx responses.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional

logger = logging.getLogger("masyg.cache")

_TRUE_VALUES = {"1", "true", "yes", "on"}


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


class OptionalRedisCache:
    """Small JSON cache with a failure circuit breaker.

    Defaults to enabled in production and disabled in development. Set
    CACHE_REDIS_ENABLED explicitly to override that policy.
    """

    def __init__(
        self,
        *,
        redis_url: Optional[str] = None,
        enabled: Optional[bool] = None,
        retry_after_seconds: float = 30.0,
        client: Any = None,
    ) -> None:
        environment = os.getenv("FAST_API_ENV", "development").lower()
        self.enabled = (
            enabled
            if enabled is not None
            else _env_flag("CACHE_REDIS_ENABLED", environment == "production")
        )
        self.retry_after_seconds = max(float(retry_after_seconds), 0.0)
        self._disabled_until = 0.0
        self._client = client

        if self.enabled and self._client is None:
            url = redis_url or os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
            try:
                import redis.asyncio as redis

                # from_url is lazy; no network request happens at import time.
                self._client = redis.from_url(url, decode_responses=True)
            except (ImportError, OSError) as exc:
                logger.warning(
                    "Redis cache unavailable at initialization; cache disabled error_type=%s",
                    type(exc).__name__,
                )
                self.enabled = False

    def _available_now(self) -> bool:
        return bool(
            self.enabled
            and self._client is not None
            and time.monotonic() >= self._disabled_until
        )

    def _record_failure(self, operation: str, exc: BaseException) -> None:
        self._disabled_until = time.monotonic() + self.retry_after_seconds
        logger.warning(
            "Redis cache %s failed; bypassing cache temporarily error_type=%s",
            operation,
            type(exc).__name__,
        )

    async def get_json(self, key: str) -> Optional[Any]:
        if not self._available_now():
            return None
        try:
            payload = await self._client.get(key)
            if payload is None:
                return None
            return json.loads(payload)
        except Exception as exc:
            self._record_failure("read", exc)
            return None

    async def set_json(self, key: str, value: Any, *, ttl_seconds: int) -> bool:
        if not self._available_now():
            return False
        try:
            await self._client.set(
                key,
                json.dumps(value, separators=(",", ":"), default=str),
                ex=max(int(ttl_seconds), 1),
            )
            return True
        except Exception as exc:
            self._record_failure("write", exc)
            return False

    async def close(self) -> None:
        if self._client is None:
            return
        close = getattr(self._client, "aclose", None)
        if close is None:
            close = getattr(self._client, "close", None)
        if close is None:
            return
        try:
            result = close()
            if hasattr(result, "__await__"):
                await result
        except Exception:
            logger.debug("Redis cache close failed", exc_info=True)


analytics_cache = OptionalRedisCache()
