from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from masyg_extractor.integrations.accounting.shared.durable_status import (
    read_accounting_durable_status,
    resolve_accounting_record_lookup,
)


ROOT = Path(__file__).resolve().parents[2]


class FakeRepo:
    def __init__(self, records=None):
        self.records = records or {}
        self.reads = []

    def get_record(
        self,
        record_type,
        group_id,
        transaction_id,
    ):
        key = (
            record_type,
            group_id,
            transaction_id,
        )
        self.reads.append(key)
        return self.records.get(key)


def status(
    repo,
    provider,
    intent,
    *,
    now=None,
):
    return read_accounting_durable_status(
        repo,
        provider=provider,
        intent=intent,
        group_id="group-1",
        file_id="file-1",
        now=now,
    )


def test_supported_provider_intents_resolve_canonical_record_types():
    assert resolve_accounting_record_lookup(
        "quickbooks",
        "create_ar_invoice",
    ).record_type == "invoices"

    assert resolve_accounting_record_lookup(
        "quickbooks",
        "create_sales_receipt",
    ).record_type == "salesreceipts"

    assert resolve_accounting_record_lookup(
        "xero",
        "create_ar_invoice",
    ).record_type == "invoices"

    assert resolve_accounting_record_lookup(
        "xero",
        "create_ap_bill",
    ).record_type == "bills"


def test_xero_supported_actions_preserve_historical_invoicess_fallback():
    assert resolve_accounting_record_lookup(
        "xero",
        "create_ar_invoice",
    ).legacy_record_types == (
        "invoicess",
    )

    assert resolve_accounting_record_lookup(
        "xero",
        "create_ap_bill",
    ).legacy_record_types == (
        "invoicess",
    )


def test_missing_record_returns_none_state():
    result = status(
        FakeRepo(),
        "quickbooks",
        "create_ar_invoice",
    )

    assert result == {
        "provider": "quickbooks",
        "intent": "create_ar_invoice",
        "group_id": "group-1",
        "file_id": "file-1",
        "record_type": "invoices",
        "status": "none",
        "provider_document_id": None,
        "provider_document_number": None,
        "completed_at": None,
        "claimed_at": None,
        "uncertain_at": None,
        "recovery_required": False,
        "recovery_reason": None,
        "legacy": False,
    }


def test_succeeded_record_returns_provider_result_metadata():
    repo = FakeRepo(
        {
            (
                "invoices",
                "group-1",
                "file-1",
            ): {
                "status": "succeeded",
                "providerDocumentId": "qb-123",
                "providerDocumentNumber": "INV-42",
                "completedAt": "2026-09-15T01:00:00Z",
            }
        }
    )

    result = status(
        repo,
        "quickbooks",
        "create_ar_invoice",
    )

    assert result["status"] == "succeeded"
    assert (
        result["provider_document_id"]
        == "qb-123"
    )
    assert (
        result["provider_document_number"]
        == "INV-42"
    )
    assert (
        result["completed_at"]
        == "2026-09-15T01:00:00Z"
    )
    assert result["legacy"] is False


def test_sending_record_remains_sending():
    repo = FakeRepo(
        {
            (
                "bills",
                "group-1",
                "file-1",
            ): {
                "status": "sending",
                "claimedAt": "2026-09-15T01:00:00Z",
            }
        }
    )

    result = status(
        repo,
        "xero",
        "create_ap_bill",
    )

    assert result["status"] == "sending"
    assert (
        result["claimed_at"]
        == "2026-09-15T01:00:00Z"
    )


def test_recent_sending_record_does_not_require_recovery():
    repo = FakeRepo(
        {
            (
                "bills",
                "group-1",
                "file-1",
            ): {
                "status": "sending",
                "claimedAt": "2026-09-15T01:45:01Z",
            }
        }
    )

    result = status(
        repo,
        "xero",
        "create_ap_bill",
        now=datetime(
            2026,
            9,
            15,
            2,
            15,
            0,
            tzinfo=timezone.utc,
        ),
    )

    assert result["status"] == "sending"
    assert result["recovery_required"] is False
    assert result["recovery_reason"] is None


def test_sending_record_becomes_recovery_required_at_exact_boundary():
    repo = FakeRepo(
        {
            (
                "bills",
                "group-1",
                "file-1",
            ): {
                "status": "sending",
                "claimedAt": "2026-09-15T01:45:00Z",
            }
        }
    )

    result = status(
        repo,
        "xero",
        "create_ap_bill",
        now=datetime(
            2026,
            9,
            15,
            2,
            15,
            0,
            tzinfo=timezone.utc,
        ),
    )

    # The durable owner stays "sending". Recovery metadata is a
    # read-model signal only; it never releases the claim.
    assert result["status"] == "sending"
    assert result["recovery_required"] is True
    assert (
        result["recovery_reason"]
        == "stale_sending"
    )


def test_sending_without_valid_claim_timestamp_fails_closed():
    for claimed_at in (
        None,
        "",
        "not-a-timestamp",
        "2026-09-15T01:00:00",
    ):
        repo = FakeRepo(
            {
                (
                    "bills",
                    "group-1",
                    "file-1",
                ): {
                    "status": "sending",
                    "claimedAt": claimed_at,
                }
            }
        )

        result = status(
            repo,
            "xero",
            "create_ap_bill",
            now=datetime(
                2026,
                9,
                15,
                2,
                15,
                0,
                tzinfo=timezone.utc,
            ),
        )

        assert result["status"] == "sending"
        assert result["recovery_required"] is True
        assert (
            result["recovery_reason"]
            == "unverifiable_sending"
        )


def test_future_sending_timestamp_fails_closed():
    repo = FakeRepo(
        {
            (
                "bills",
                "group-1",
                "file-1",
            ): {
                "status": "sending",
                "claimedAt": "2026-09-15T02:15:01Z",
            }
        }
    )

    result = status(
        repo,
        "xero",
        "create_ap_bill",
        now=datetime(
            2026,
            9,
            15,
            2,
            15,
            0,
            tzinfo=timezone.utc,
        ),
    )

    assert result["status"] == "sending"
    assert result["recovery_required"] is True
    assert (
        result["recovery_reason"]
        == "unverifiable_sending"
    )


def test_uncertain_record_remains_uncertain():
    repo = FakeRepo(
        {
            (
                "bills",
                "group-1",
                "file-1",
            ): {
                "status": "uncertain",
                "uncertainAt": "2026-09-15T01:00:00Z",
            }
        }
    )

    result = status(
        repo,
        "xero",
        "create_ap_bill",
    )

    assert result["status"] == "uncertain"
    assert (
        result["uncertain_at"]
        == "2026-09-15T01:00:00Z"
    )


def test_historical_xero_ap_record_blocks_as_legacy_success():
    repo = FakeRepo(
        {
            (
                "invoicess",
                "group-1",
                "file-1",
            ): {
                "integration": "xero",
                "docNumber": "OLD-42",
            }
        }
    )

    result = status(
        repo,
        "xero",
        "create_ap_bill",
    )

    assert result["status"] == "succeeded"
    assert result["record_type"] == "invoicess"
    assert result["legacy"] is True
    assert (
        result["provider_document_number"]
        == "OLD-42"
    )



def test_historical_xero_zero_suffix_is_found_from_canonical_file_id():
    repo = FakeRepo(
        {
            (
                "invoicess",
                "group-1",
                "file-1-0",
            ): {
                "integration": "xero",
                "docNumber": "LEGACY-BILL-42",
            }
        }
    )

    result = status(
        repo,
        "xero",
        "create_ap_bill",
    )

    assert result["status"] == "succeeded"
    assert result["record_type"] == "invoicess"
    assert result["legacy"] is True

    # The public response must preserve the canonical identity,
    # never expose the old storage alias as the document identity.
    assert result["file_id"] == "file-1"

    assert (
        result["provider_document_number"]
        == "LEGACY-BILL-42"
    )

    assert repo.reads == [
        (
            "bills",
            "group-1",
            "file-1",
        ),
        (
            "invoicess",
            "group-1",
            "file-1",
        ),
        (
            "invoicess",
            "group-1",
            "file-1-0",
        ),
    ]


def test_canonical_xero_record_wins_over_legacy_fallback():
    repo = FakeRepo(
        {
            (
                "bills",
                "group-1",
                "file-1",
            ): {
                "status": "uncertain",
            },
            (
                "invoicess",
                "group-1",
                "file-1",
            ): {
                "docNumber": "OLD-42",
            },
        }
    )

    result = status(
        repo,
        "xero",
        "create_ap_bill",
    )

    assert result["status"] == "uncertain"
    assert result["record_type"] == "bills"
    assert result["legacy"] is False

    assert repo.reads == [
        (
            "bills",
            "group-1",
            "file-1",
        )
    ]


def test_historical_canonical_record_without_status_is_success():
    repo = FakeRepo(
        {
            (
                "invoices",
                "group-1",
                "file-1",
            ): {
                "integration": "quickbooks",
                "docNumber": "INV-OLD",
            }
        }
    )

    result = status(
        repo,
        "quickbooks",
        "create_ar_invoice",
    )

    assert result["status"] == "succeeded"
    assert result["legacy"] is True


def test_unsupported_provider_intent_is_rejected():
    with pytest.raises(
        ValueError,
        match="Unsupported accounting provider/intent",
    ):
        resolve_accounting_record_lookup(
            "quickbooks",
            "create_ap_bill",
        )


def test_shared_status_route_is_authenticated_and_registered_once():
    router = (
        ROOT
        / "masyg_extractor/integrations/accounting/shared/"
          "status_router.py"
    ).read_text()

    routes = (
        ROOT
        / "masyg_extractor/routes/__init__.py"
    ).read_text()

    assert (
        'prefix="/integrations/accounting"'
        in router
    )
    assert '@router.get("/status")' in router
    assert "get_current_user_from_cookie" in router
    assert "read_accounting_durable_status" in router

    assert (
        "accounting_status_router"
        in routes
    )
    assert (
        "app.include_router("
        "accounting_status_router, prefix=\"\")"
        in routes
    )


def test_status_endpoint_never_calls_external_accounting_provider():
    router = (
        ROOT
        / "masyg_extractor/integrations/accounting/shared/"
          "status_router.py"
    ).read_text()

    assert "QuickBooksClientAdapter" not in router
    assert "XeroClientAdapter" not in router
    assert "xero_request" not in router
    assert "requests." not in router
    assert "httpx." not in router
