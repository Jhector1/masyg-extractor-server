import asyncio
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class FakeItemRepository:
    def __init__(self, item_exists=True):
        self.item_exists = item_exists

    def get_item(self, item_id):
        if not self.item_exists:
            return None
        return {"itemId": item_id}


class FakeService:
    def __init__(self, user_id, *, item_exists=True, fail=False):
        self.user_id = user_id
        self.repository = FakeItemRepository(item_exists=item_exists)
        self.fail = fail
        self.synced = []
        self.refreshed = []

    async def sync_item(self, item_id):
        if self.fail:
            raise RuntimeError("sync failed")
        self.synced.append(item_id)
        return {"added": 1, "modified": 0, "removed": 0}

    async def refresh_item_status(self, item_id):
        if self.fail:
            raise RuntimeError("refresh failed")
        self.refreshed.append(item_id)
        return {"health": "healthy"}


class FakeWebhookRepository:
    def __init__(self, claimed):
        self.claimed = dict(claimed)
        self.processed = []
        self.retried = []

    def claim_event(self, event_id):
        result = dict(self.claimed)
        result["eventId"] = event_id
        return result

    def mark_processed(self, event_id, claim_token, *, outcome):
        self.processed.append(
            (event_id, claim_token, outcome)
        )
        return True

    def mark_retry(
        self,
        event_id,
        claim_token,
        *,
        attempt_count,
        error,
    ):
        self.retried.append(
            (
                event_id,
                claim_token,
                attempt_count,
                error,
            )
        )
        return True


def _claimed(webhook_type, webhook_code):
    return {
        "claimToken": "claim-1",
        "attemptCount": 1,
        "userId": "user-1",
        "itemId": "item-1",
        "webhookType": webhook_type,
        "webhookCode": webhook_code,
    }


def test_transactions_webhook_syncs_only_the_target_item():
    from masyg_extractor.integrations.bank.webhook_processor import (
        BankWebhookProcessor,
    )

    repo = FakeWebhookRepository(
        _claimed(
            "TRANSACTIONS",
            "SYNC_UPDATES_AVAILABLE",
        )
    )
    services = []

    def factory(user_id):
        service = FakeService(user_id)
        services.append(service)
        return service

    result = asyncio.run(
        BankWebhookProcessor(
            repo,
            service_factory=factory,
        ).process_event("event-1")
    )

    assert result["outcome"] == "transactions_synced"
    assert services[0].synced == ["item-1"]
    assert services[0].refreshed == []
    assert repo.processed == [
        ("event-1", "claim-1", "transactions_synced")
    ]


def test_item_webhook_refreshes_current_item_state():
    from masyg_extractor.integrations.bank.webhook_processor import (
        BankWebhookProcessor,
    )

    repo = FakeWebhookRepository(
        _claimed("ITEM", "ERROR")
    )
    services = []

    def factory(user_id):
        service = FakeService(user_id)
        services.append(service)
        return service

    result = asyncio.run(
        BankWebhookProcessor(
            repo,
            service_factory=factory,
        ).process_event("event-2")
    )

    assert result["outcome"] == "item_status_refreshed"
    assert services[0].refreshed == ["item-1"]
    assert services[0].synced == []


def test_unsupported_webhook_is_durably_ignored():
    from masyg_extractor.integrations.bank.webhook_processor import (
        BankWebhookProcessor,
    )

    repo = FakeWebhookRepository(
        _claimed("AUTH", "DEFAULT_UPDATE")
    )

    service = FakeService("user-1")

    result = asyncio.run(
        BankWebhookProcessor(
            repo,
            service_factory=lambda _user_id: service,
        ).process_event("event-3")
    )

    assert result["outcome"] == "ignored"
    assert service.synced == []
    assert service.refreshed == []
    assert repo.processed[-1][2] == "ignored"


def test_processing_failure_returns_event_to_retry_state():
    from masyg_extractor.integrations.bank.webhook_processor import (
        BankWebhookProcessor,
    )

    repo = FakeWebhookRepository(
        _claimed(
            "TRANSACTIONS",
            "SYNC_UPDATES_AVAILABLE",
        )
    )

    result = asyncio.run(
        BankWebhookProcessor(
            repo,
            service_factory=lambda user_id: FakeService(
                user_id,
                fail=True,
            ),
        ).process_event("event-4")
    )

    assert result["retry"] is True
    assert repo.processed == []
    assert len(repo.retried) == 1
    assert repo.retried[0][0] == "event-4"
    assert repo.retried[0][1] == "claim-1"
    assert repo.retried[0][2] == 1
    assert "RuntimeError" in repo.retried[0][3]


def test_claim_owner_is_transactional_and_has_restart_lease():
    source = (
        ROOT
        / "masyg_extractor/integrations/bank/webhook_repository.py"
    ).read_text()

    start = source.index("    def claim_event(")
    end = source.index("    def mark_processed(", start)
    block = source[start:end]

    assert "@firestore.transactional" in block
    assert 'processing_status == "processing"' in block
    assert "leaseExpiresAt" in block
    assert "nextAttemptAt" in block
    assert '"processingStatus": "processing"' in block
    assert "attemptCount" in block
    assert "claimToken" in block


def test_bank_service_exposes_single_item_sync_owner():
    service = (
        ROOT
        / "masyg_extractor/integrations/bank/service.py"
    ).read_text()

    assert "async def sync_item(self, item_id: str)" in service
    assert "return await self._sync_item(item)" in service

    start = service.index("    async def sync_transactions(")
    block = service[start:]
    assert "result = await self.sync_item(item_id)" in block


def test_existing_scheduler_owns_webhook_consumer_and_owner_backfill():
    server = (ROOT / "server.py").read_text()
    repository = (
        ROOT
        / "masyg_extractor/integrations/bank/webhook_repository.py"
    ).read_text()

    assert "IntervalTrigger(seconds=30)" in server
    assert 'id="bank_webhook_inbox"' in server
    assert "process_pending_bank_webhooks" in server
    assert "max_instances=1" in server
    assert "owner_backfill = await asyncio.to_thread" in server
    assert "BankWebhookRepository().backfill_item_owners" in server

    assert 'collection_group("items")' in repository
    assert 'path[3] != "bank"' in repository
    assert "register_item_owner(item_id, user_id)" in repository
