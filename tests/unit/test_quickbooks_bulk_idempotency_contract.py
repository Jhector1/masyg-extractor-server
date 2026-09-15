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
    normalized = "".join(bulk.split())

    claim = normalized.index(
        "self.repo.claim_record"
    )
    request = normalized.index(
        "quickbooks_response=(awaitself.client.request("
    )

    assert claim < request
    assert (
        "provider_started_bids.update("
        in bulk
    )
    assert (
        "provider_request_started"
        not in bulk
    )


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
    normalized = "".join(bulk.split())

    assert "provider_entity=" in normalized
    assert (
        "payload.get(self.doc_type)"
        in normalized
    )
    assert '"providerDocumentId"' in bulk
    assert "self.repo.finalize_record" in bulk


def test_quickbooks_fault_releases_claim_but_transport_error_does_not():
    bulk = bulk_source()
    normalized = "".join(bulk.split())

    fault = normalized.index(
        'if"Fault"inpayload:'
    )
    release = normalized.index(
        "self.repo.release_record_claim",
        fault,
    )

    top_error = normalized.index(
        'ifquickbooks_response.get("error"):'
    )
    uncertain = normalized.index(
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

def test_quickbooks_bulk_uses_shared_document_chunk_policy():
    bulk = bulk_source()

    assert (
        "chunk_accounting_provider_documents("
        in bulk
    )
    assert '"quickbooks"' in bulk
    assert (
        "for (\n"
        "                chunk_index,\n"
        "                document_chunk,"
        in bulk
    )
    assert (
        '"BatchItemRequest":\n'
        '                        document_chunk'
        in bulk
    )


def test_quickbooks_tracks_provider_start_per_document():
    bulk = bulk_source()

    assert (
        "provider_started_bids: set[str] = set()"
        in bulk
    )

    token = bulk.index(
        "quickbooks_token = ("
    )
    started = bulk.index(
        "provider_started_bids.update(",
        token,
    )
    request = bulk.index(
        "quickbooks_response = (",
        started,
    )

    assert token < started < request


def test_quickbooks_transport_error_releases_only_unsent_later_chunks():
    bulk = bulk_source()
    normalized = "".join(bulk.split())

    top_error = normalized.index(
        "ifquickbooks_response.get("
    )

    uncertain = normalized.index(
        "self.repo.mark_record_uncertain",
        top_error,
    )

    unsent_guard = normalized.index(
        "pending_bidinprovider_started_bids",
        uncertain,
    )

    release = normalized.index(
        "self.repo.release_record_claim",
        unsent_guard,
    )

    assert (
        top_error
        < uncertain
        < unsent_guard
        < release
    )

    assert (
        "QuickBooks batch stopped "
        in bulk
    )
    assert (
        "before this document was "
        in bulk
    )
    assert '"sent."' in bulk


def test_quickbooks_exception_cleanup_uses_per_document_provider_start():
    bulk = bulk_source()

    exception = bulk.index(
        "except Exception as e:"
    )

    started_guard = bulk.index(
        "if bid in provider_started_bids:",
        exception,
    )

    uncertain = bulk.index(
        "self.repo.mark_record_uncertain",
        started_guard,
    )

    release = bulk.index(
        "self.repo.release_record_claim",
        uncertain,
    )

    assert (
        started_guard
        < uncertain
        < release
    )


def test_quickbooks_missing_results_are_scoped_to_started_chunk():
    bulk = bulk_source()

    missing = bulk.index(
        "# Missing result from a provider-started chunk"
    )

    assert (
        "chunk_invoice_records.items()"
        in bulk[missing:]
    )

    assert (
        "self.repo.mark_record_uncertain"
        in bulk[missing:]
    )
