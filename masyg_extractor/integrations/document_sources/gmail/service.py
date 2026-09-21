from __future__ import annotations

import asyncio

from masyg_extractor.integrations.document_sources.gmail.client import (
    GmailConfigurationError,
    GmailOAuthError,
    refresh_access_token,
    start_gmail_watch,
    stop_gmail_watch,
)
from masyg_extractor.integrations.document_sources.gmail.repository import (
    GmailCredentialRepository,
)
from masyg_extractor.services.my_log import logger
from masyg_extractor.services.subscription_access import (
    user_has_active_subscription,
)


async def refresh_gmail_authorization(
    user_id: str,
) -> str:
    repository = GmailCredentialRepository(user_id)
    refresh_token = repository.refresh_token()

    if not refresh_token:
        raise GmailOAuthError("Gmail is not connected")

    token = await refresh_access_token(refresh_token)
    repository.store_refreshed_access_token(
        access_token=token["access_token"],
        expires_in=token["expires_in"],
    )
    return token["access_token"]


async def ensure_gmail_watch(
    user_id: str,
) -> dict:
    repository = GmailCredentialRepository(user_id)

    if not await asyncio.to_thread(
        repository.is_connected
    ):
        raise GmailOAuthError("Gmail is not connected")

    await asyncio.to_thread(
        repository.ensure_mailbox_owner
    )

    if not await user_has_active_subscription(
        user_id
    ):
        return {
            "active": False,
            "reason": "subscription_required",
        }

    access_token = await refresh_gmail_authorization(
        user_id
    )
    watch = await start_gmail_watch(access_token)

    await asyncio.to_thread(
        repository.store_watch_state,
        history_id=watch["history_id"],
        expiration_ms=watch["expiration_ms"],
        topic=watch["topic"],
    )

    return {
        "active": True,
        **watch,
    }


async def stop_gmail_watch_for_user(
    user_id: str,
) -> None:
    repository = GmailCredentialRepository(user_id)

    if not await asyncio.to_thread(
        repository.is_connected
    ):
        return

    access_token = await refresh_gmail_authorization(
        user_id
    )
    await stop_gmail_watch(access_token)
    await asyncio.to_thread(
        repository.mark_watch_stopped
    )


async def renew_gmail_watches() -> None:
    user_ids = await asyncio.to_thread(
        GmailCredentialRepository.registered_user_ids
    )

    for user_id in user_ids:
        try:
            active = await user_has_active_subscription(
                user_id
            )

            if active:
                await ensure_gmail_watch(user_id)
                continue

            repository = GmailCredentialRepository(
                user_id
            )
            watch = await asyncio.to_thread(
                repository.watch_state
            )
            if watch.get("status") == "active":
                try:
                    await stop_gmail_watch_for_user(
                        user_id
                    )
                except Exception as exc:
                    logger.warning(
                        "Unable to stop inactive Gmail "
                        "watch user_id=%s error_type=%s",
                        user_id,
                        type(exc).__name__,
                    )
        except GmailConfigurationError:
            logger.info(
                "Gmail watch renewal skipped: "
                "Pub/Sub topic is not configured"
            )
            return
        except Exception as exc:
            logger.warning(
                "Gmail watch renewal failed "
                "user_id=%s error_type=%s",
                user_id,
                type(exc).__name__,
            )
