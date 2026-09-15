import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

SERVICE = (
    ROOT
    / "masyg_extractor/integrations/accounting/xero/"
      "services/document_service.py"
)

ROUTER = (
    ROOT
    / "masyg_extractor/integrations/accounting/xero/router.py"
)


def bulk_source() -> str:
    source = SERVICE.read_text()
    tree = ast.parse(source)

    document_service = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "DocumentService"
    )

    method = next(
        node
        for node in document_service.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "send_document_in_bulk"
    )

    lines = source.splitlines()

    return "\n".join(
        lines[
            method.lineno - 1:
            method.end_lineno
        ]
    )


def test_xero_bulk_uses_canonical_ar_and_ap_record_types():
    source = SERVICE.read_text()

    assert (
        '"bills"'
        in source
    )
    assert (
        '"invoices"'
        in source
    )
    assert (
        'invoice_status or "").upper() == "ACCPAY"'
        in source
    )


def test_xero_receipt_route_remains_ap_bill_action():
    router = ROUTER.read_text()

    assert '"/send-receipt-in-bulk"' in router
    assert "invoice_status='ACCPAY'" in router


def test_xero_preserves_historical_invoicess_lookup():
    source = SERVICE.read_text()

    assert (
        '_xero_legacy_accounting_record_type'
        in source
    )
    assert (
        'return f"{str(doc_type or \'\').lower()}s"'
        in source
    )
    assert (
        'legacy_record_type != record_type'
        in source
    )


def test_xero_bulk_claims_before_provider_request():
    bulk = bulk_source()

    claim_pos = bulk.index(
        "self.repo.claim_record"
    )
    request_pos = bulk.index(
        "xero_response = await self.client.request("
    )

    assert claim_pos < request_pos
    assert (
        "provider_request_started = True"
        in bulk
    )


def test_xero_bulk_sends_only_owned_claims():
    bulk = bulk_source()

    assert "claimed_payloads" in bulk
    assert "claimed_documents" in bulk
    assert "claimed_invoice_records" in bulk

    assert (
        "document_payload_bulk = claimed_payloads"
        in bulk
    )
    assert (
        "prepared_documents = claimed_documents"
        in bulk
    )
    assert (
        "invoice_records = claimed_invoice_records"
        in bulk
    )


def test_xero_confirmed_success_finalizes_same_claim():
    bulk = bulk_source()

    assert "self.repo.finalize_record" in bulk
    assert '"providerDocumentId"' in bulk
    assert '"InvoiceID"' in bulk


def test_xero_confirmed_rejection_releases_claim():
    bulk = bulk_source()

    assert "self.repo.release_record_claim" in bulk
    assert "_xero_response_invoice_error" in bulk


def test_xero_ambiguous_result_keeps_duplicate_barrier():
    source = SERVICE.read_text()
    bulk = bulk_source()

    assert (
        "def _xero_provider_error_is_ambiguous("
        in source
    )
    assert "return status_code >= 500" in source
    assert "self.repo.mark_record_uncertain" in bulk
    assert (
        "Xero did not return a result for this document."
        in bulk
    )


def test_xero_bulk_no_longer_plain_stores_success_over_claim():
    bulk = bulk_source()

    assert (
        "store_records_in_firebase(successful_records)"
        not in bulk
    )
    assert "successful_records" not in bulk
