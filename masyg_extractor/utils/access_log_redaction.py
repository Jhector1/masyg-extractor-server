from __future__ import annotations

import logging


_SENSITIVE_CALLBACK_PATHS = frozenset(
    {
        "/integrations/gmail/callback",
        "/integrations/google-drive/callback",
    }
)


class SensitiveOAuthAccessLogFilter(logging.Filter):
    # Redact OAuth callback query strings from Uvicorn access logs.

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 3:
            return True

        full_path = args[2]
        if not isinstance(full_path, str) or "?" not in full_path:
            return True

        path = full_path.split("?", 1)[0]
        if path not in _SENSITIVE_CALLBACK_PATHS:
            return True

        redacted = list(args)
        redacted[2] = f"{path}?[REDACTED]"
        record.args = tuple(redacted)
        return True


def install_sensitive_oauth_access_log_filter() -> None:
    access_logger = logging.getLogger("uvicorn.access")
    if any(
        isinstance(item, SensitiveOAuthAccessLogFilter)
        for item in access_logger.filters
    ):
        return
    access_logger.addFilter(SensitiveOAuthAccessLogFilter())
