"""Transactional mail delivery helpers with failure containment."""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("masyg.mail")


def _mail_debug_enabled() -> bool:
    return (os.getenv("MAIL_DEBUG") or "").strip().lower() in {"1", "true", "yes", "on"}


async def send_message_safely(mail: Any, message: Any) -> bool:
    """Send transactional mail without leaking provider failure into ASGI.

    Returns True on delivery and False on provider/transport failure. Starlette
    BackgroundTask ignores the return value, while tests and future queue workers can
    use it. Full tracebacks are opt-in with MAIL_DEBUG=1 so expected provider outages
    do not flood normal application logs.
    """
    try:
        await mail.send_message(message)
        return True
    except Exception as exc:
        recipients = getattr(message, "recipients", None) or []
        subject = getattr(message, "subject", "<unknown>")
        args = (subject, len(recipients), type(exc).__name__, str(exc)[:300])
        if _mail_debug_enabled():
            logger.exception(
                "Transactional email delivery failed subject=%r recipient_count=%d "
                "error_type=%s error=%s",
                *args,
            )
        else:
            logger.error(
                "Transactional email delivery failed subject=%r recipient_count=%d "
                "error_type=%s error=%s",
                *args,
            )
        return False
