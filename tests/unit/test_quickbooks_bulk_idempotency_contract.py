import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SERVICE = (
    ROOT
    / "masyg_extractor/integrations/accounting/quickbooks/"
      "services/document_service.py"
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
        lines[method.lineno - 1 : method.end_lineno]
    )


def test_quickbooks_bulk_claims_before_accounting_document_request():
    bulk = bulk_source()

    claim = bulk.index("self.repo.claim_record")
    request = bulk.index(
        "quickbooks_response = await self.client.request("
    )

    assert claim < request
    assert "provider_request_started = True" in bulk


def test_quickbooks_bulk_only_sends_claimed_payloads():
    bulk = bulk_source()

    assert "claimed_payloads" in bulk
    assert "claimed_invoice_records" in bulk
    assert "document_payload_bulk = claimed_payloads" in bulk
    assert "invoice_records = claimed_invoice_records" in bulk


def test_quickbooks_bulk_settles_all_claim_outcomes():
    bulk = bulk_source()

    assert "self.repo.finalize_record" in bulk
    assert "self.repo.release_record_claim" in bulk
    assert "self.repo.mark_record_uncertain" in bulk


def test_quickbooks_confirmed_success_captures_provider_identity():
    bulk = bulk_source()

    assert 'provider_entity = payload.get(self.doc_type)' in bulk
    assert '"providerDocumentId"' in bulk
    assert "self.repo.finalize_record" in bulk


def test_quickbooks_fault_releases_claim_but_transport_error_does_not():
    bulk = bulk_source()

    fault = bulk.index('if "Fault" in payload:')
    release = bulk.index(
        "self.repo.release_record_claim",
        fault,
    )

    top_error = bulk.index(
        'if quickbooks_response.get("error"):'
    )
    uncertain = bulk.index(
        "self.repo.mark_record_uncertain",
        top_error,
    )

    assert release > fault
    assert uncertain > top_error


def test_quickbooks_bulk_no_longer_overwrites_claim_with_plain_store():
    bulk = bulk_source()

    assert (
        "await self.store_records_in_firebase(firestore_records)"
        not in bulk
    )
    assert "firestore_records.append(inv)" not in bulk
