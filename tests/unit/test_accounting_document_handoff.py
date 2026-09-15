from __future__ import annotations

from pathlib import Path

import pytest

from masyg_extractor.integrations.accounting.shared.document_handoff import (
    resolve_accounting_document_handoff,
)


ROOT = Path(__file__).resolve().parents[2]


def resolve(document, **identity):
    return resolve_accounting_document_handoff(
        document,
        group_id=identity.get(
            "group_id",
            "group-1",
        ),
        file_id=identity.get(
            "file_id",
            "file-1",
        ),
    )


def test_vendor_bill_uses_backend_canonical_ap_intent():
    result = resolve(
        {
            "documentType": "vendor_bill",
            "status": "ok",
        }
    )

    assert result == {
        "group_id": "group-1",
        "file_id": "file-1",
        "document_type": "vendor_bill",
        "accounting_intent": "create_ap_bill",
    }


def test_sales_invoice_uses_backend_canonical_ar_intent():
    result = resolve(
        {
            "documentType": "sales_invoice",
        }
    )

    assert (
        result["accounting_intent"]
        == "create_ar_invoice"
    )


def test_legacy_invoice_keeps_existing_vendor_bill_semantics():
    result = resolve(
        {
            "documentType": "invoice",
        }
    )

    assert result["document_type"] == "vendor_bill"
    assert (
        result["accounting_intent"]
        == "create_ap_bill"
    )


def test_unknown_document_requires_review_instead_of_guessing():
    result = resolve(
        {
            "documentType": "mystery-document",
        }
    )

    assert result["document_type"] == "other"
    assert (
        result["accounting_intent"]
        == "review_required"
    )


def test_canonical_document_identity_is_preserved():
    result = resolve(
        {
            "documentType": "vendor_bill",
        },
        group_id="20260912072644",
        file_id=(
            "e9ef5d6a-ac9f-4433-82b7-"
            "08236b931d83_yyyo_pdf"
        ),
    )

    assert (
        result["group_id"]
        == "20260912072644"
    )
    assert result["file_id"].endswith(
        "_yyyo_pdf"
    )
    assert not result["file_id"].endswith(
        "-0"
    )


@pytest.mark.parametrize(
    "document",
    [
        {
            "documentType": "vendor_bill",
            "trashed": True,
        },
        {
            "documentType": "vendor_bill",
            "status": "failed",
        },
        {
            "documentType": "vendor_bill",
            "error": "extraction failed",
        },
    ],
)
def test_ineligible_documents_cannot_enter_accounting(
    document,
):
    with pytest.raises(ValueError):
        resolve(document)


def test_shared_handoff_endpoint_is_authenticated_and_document_scoped():
    router = (
        ROOT
        / "masyg_extractor/integrations/accounting/shared/"
          "status_router.py"
    ).read_text()

    assert '@router.get("/handoff")' in router

    assert (
        "get_current_user_from_cookie"
        in router
    )

    assert (
        '.collection("users")'
        in router
    )
    assert (
        ".document(user_id)"
        in router
    )
    assert (
        '.collection("groups")'
        in router
    )
    assert (
        ".document(group_id)"
        in router
    )
    assert (
        '.collection("files")'
        in router
    )
    assert (
        ".document(file_id)"
        in router
    )


def test_handoff_reuses_backend_document_intent_owner():
    owner = (
        ROOT
        / "masyg_extractor/integrations/accounting/shared/"
          "document_handoff.py"
    ).read_text()

    assert "default_accounting_intent" in owner
    assert (
        "DEFAULT_ACCOUNTING_INTENT_BY_DOCUMENT_TYPE"
        not in owner
    )

    assert "quickbooks" not in owner.lower()
    assert "xero" not in owner.lower()


def test_handoff_does_not_call_external_accounting_providers():
    owner = (
        ROOT
        / "masyg_extractor/integrations/accounting/shared/"
          "document_handoff.py"
    ).read_text()

    router = (
        ROOT
        / "masyg_extractor/integrations/accounting/shared/"
          "status_router.py"
    ).read_text()

    forbidden = (
        "QuickBooksClientAdapter",
        "XeroClientAdapter",
        "xero_request",
        "requests.",
        "httpx.",
    )

    for value in forbidden:
        assert value not in owner
        assert value not in router
