from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from masyg_extractor.integrations.bank.service import BankService
from masyg_extractor.integrations.bank.webhook_repository import (
    BankWebhookRepository,
)
from masyg_extractor.services.my_log import logger


class BankWebhookProcessor:
    def __init__(
        self,
        repository: BankWebhookRepository | None = None,
        *,
        service_factory: Callable[[str], Any] = BankService,
    ) -> None:
        self.repository = (
            repository
            if repository is not None
            else BankWebhookRepository()
        )
        self.service_factory = service_factory

    async def process_event(
        self,
        event_id: str,
    ) -> dict[str, Any]:
        claimed = await asyncio.to_thread(
            self.repository.claim_event,
            event_id,
        )

        if not claimed:
            return {
                "event_id": event_id,
                "claimed": False,
                "processed": False,
            }

        claim_token = str(claimed.get("claimToken") or "")
        attempt_count = int(claimed.get("attemptCount") or 1)
        user_id = str(claimed.get("userId") or "").strip()
        item_id = str(claimed.get("itemId") or "").strip()
        webhook_type = str(
            claimed.get("webhookType") or ""
        ).strip().upper()
        webhook_code = str(
            claimed.get("webhookCode") or ""
        ).strip().upper()

        service = self.service_factory(user_id)

        try:
            # Re-check ownership at execution time. A webhook can sit in the
            # inbox while the user disconnects the Item.
            item = await asyncio.to_thread(
                service.repository.get_item,
                item_id,
            )

            if not item:
                await asyncio.to_thread(
                    self.repository.mark_processed,
                    event_id,
                    claim_token,
                    outcome="item_missing",
                )
                return {
                    "event_id": event_id,
                    "claimed": True,
                    "processed": True,
                    "outcome": "item_missing",
                }

            if (
                webhook_type == "TRANSACTIONS"
                and webhook_code == "SYNC_UPDATES_AVAILABLE"
            ):
                await service.sync_item(item_id)
                outcome = "transactions_synced"

            elif webhook_type == "ITEM":
                # ITEM deliveries can arrive out of order. Re-read Plaid's
                # current Item state instead of treating webhook payload order
                # as authoritative application state.
                await service.refresh_item_status(item_id)
                outcome = "item_status_refreshed"

            else:
                # Unsupported/legacy webhook types are acknowledged durably
                # but do not mutate bank state.
                outcome = "ignored"

            await asyncio.to_thread(
                self.repository.mark_processed,
                event_id,
                claim_token,
                outcome=outcome,
            )

            return {
                "event_id": event_id,
                "claimed": True,
                "processed": True,
                "outcome": outcome,
            }

        except KeyError:
            # Item was removed between the ownership check and service call.
            await asyncio.to_thread(
                self.repository.mark_processed,
                event_id,
                claim_token,
                outcome="item_missing",
            )
            return {
                "event_id": event_id,
                "claimed": True,
                "processed": True,
                "outcome": "item_missing",
            }

        except Exception as exc:
            logger.exception(
                "Plaid webhook processing failed "
                "event_id=%s item_id=%s type=%s code=%s",
                event_id,
                item_id,
                webhook_type,
                webhook_code,
            )

            await asyncio.to_thread(
                self.repository.mark_retry,
                event_id,
                claim_token,
                attempt_count=attempt_count,
                error=f"{type(exc).__name__}: {exc}",
            )

            return {
                "event_id": event_id,
                "claimed": True,
                "processed": False,
                "retry": True,
            }


async def process_pending_bank_webhooks(
    *,
    limit: int = 25,
) -> dict[str, int]:
    repository = BankWebhookRepository()

    # First repair webhook rows that arrived before their reverse owner index
    # was available.
    unresolved_ids = await asyncio.to_thread(
        repository.list_unresolved_event_ids,
        limit=limit,
    )
    routed = 0

    for event_id in unresolved_ids:
        if await asyncio.to_thread(
            repository.try_route_event,
            event_id,
        ):
            routed += 1

    candidate_ids = await asyncio.to_thread(
        repository.list_candidate_event_ids,
        limit=limit,
    )

    processor = BankWebhookProcessor(repository)
    stats = {
        "routed": routed,
        "candidates": len(candidate_ids),
        "claimed": 0,
        "processed": 0,
        "retried": 0,
    }

    # Deliberately process sequentially. Multiple webhook receipts for the
    # same Item may be present; serial execution avoids competing cursor writes
    # while /transactions/sync itself reconciles from the canonical cursor.
    for event_id in candidate_ids:
        result = await processor.process_event(event_id)

        if result.get("claimed"):
            stats["claimed"] += 1
        if result.get("processed"):
            stats["processed"] += 1
        if result.get("retry"):
            stats["retried"] += 1

    if any(stats.values()):
        logger.info(
            "Plaid webhook inbox processed "
            "routed=%s candidates=%s claimed=%s processed=%s retried=%s",
            stats["routed"],
            stats["candidates"],
            stats["claimed"],
            stats["processed"],
            stats["retried"],
        )

    return stats
