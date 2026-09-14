import asyncio

import pytest

from masyg_extractor.integrations.bank.reconciliation import (
    BankReconciliationService,
)


def service_with_fake_status_handler(handler):
    service = object.__new__(BankReconciliationService)
    service.set_status = handler
    return service


def test_bulk_status_reuses_single_status_owner():
    calls = []

    async def set_status(
        *,
        item_id,
        transaction_id,
        reconciliation_status,
    ):
        calls.append(
            (
                item_id,
                transaction_id,
                reconciliation_status,
            )
        )
        return {
            "updated": True,
            "transaction_id": transaction_id,
        }

    service = service_with_fake_status_handler(
        set_status
    )

    result = asyncio.run(
        service.bulk_set_status(
            identities=[
                {
                    "item_id": "item-1",
                    "transaction_id": "tx-1",
                },
                {
                    "item_id": "item-1",
                    "transaction_id": "tx-2",
                },
            ],
            reconciliation_status="ignored",
        )
    )

    assert calls == [
        ("item-1", "tx-1", "ignored"),
        ("item-1", "tx-2", "ignored"),
    ]
    assert result["requested"] == 2
    assert result["updated"] == 2
    assert result["failed"] == 0
    assert result["reconciliation_status"] == "ignored"
    assert [row["status"] for row in result["results"]] == [
        "ok",
        "ok",
    ]


def test_bulk_status_reports_missing_transaction_without_hiding_successes():
    calls = []

    async def set_status(
        *,
        item_id,
        transaction_id,
        reconciliation_status,
    ):
        calls.append(transaction_id)

        if transaction_id == "missing":
            raise KeyError("Bank transaction not found.")

        return {
            "updated": True,
            "transaction_id": transaction_id,
        }

    service = service_with_fake_status_handler(
        set_status
    )

    result = asyncio.run(
        service.bulk_set_status(
            identities=[
                {
                    "item_id": "item-1",
                    "transaction_id": "tx-1",
                },
                {
                    "item_id": "item-1",
                    "transaction_id": "missing",
                },
                {
                    "item_id": "item-1",
                    "transaction_id": "tx-3",
                },
            ],
            reconciliation_status="transfer",
        )
    )

    assert calls == ["tx-1", "missing", "tx-3"]
    assert result["requested"] == 3
    assert result["updated"] == 2
    assert result["failed"] == 1

    assert result["results"][1] == {
        "item_id": "item-1",
        "transaction_id": "missing",
        "status": "error",
        "error": "not_found",
    }


def test_bulk_status_rejects_duplicate_identity_explicitly():
    calls = []

    async def set_status(
        *,
        item_id,
        transaction_id,
        reconciliation_status,
    ):
        calls.append(transaction_id)
        return {"updated": True}

    service = service_with_fake_status_handler(
        set_status
    )

    result = asyncio.run(
        service.bulk_set_status(
            identities=[
                {
                    "item_id": "item-1",
                    "transaction_id": "tx-1",
                },
                {
                    "item_id": "item-1",
                    "transaction_id": "tx-1",
                },
            ],
            reconciliation_status="missing_document",
        )
    )

    assert calls == ["tx-1"]
    assert result["updated"] == 1
    assert result["failed"] == 1
    assert result["results"][1]["error"] == "duplicate"


def test_bulk_status_reuses_manual_status_allowlist():
    async def should_not_run(**kwargs):
        raise AssertionError(
            "set_status must not run for an invalid status"
        )

    service = service_with_fake_status_handler(
        should_not_run
    )

    with pytest.raises(
        ValueError,
        match="Reconciliation status must be",
    ):
        asyncio.run(
            service.bulk_set_status(
                identities=[
                    {
                        "item_id": "item-1",
                        "transaction_id": "tx-1",
                    }
                ],
                reconciliation_status="matched",
            )
        )


@pytest.mark.parametrize(
    "identities",
    [
        [],
        [
            {
                "item_id": "item-1",
                "transaction_id": f"tx-{index}",
            }
            for index in range(101)
        ],
    ],
)
def test_bulk_status_enforces_request_size(identities):
    async def should_not_run(**kwargs):
        raise AssertionError(
            "set_status must not run for an invalid batch"
        )

    service = service_with_fake_status_handler(
        should_not_run
    )

    with pytest.raises(
        ValueError,
        match="between 1 and 100",
    ):
        asyncio.run(
            service.bulk_set_status(
                identities=identities,
                reconciliation_status="ignored",
            )
        )
