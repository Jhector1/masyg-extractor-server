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


def test_quickbooks_bulk_claims_before_all_provider_mutations():
    bulk = bulk_source()
    normalized = "".join(bulk.split())

    claim = normalized.index(
        "self.repo.claim_record"
    )

    customer_create = normalized.index(
        "self.customer_service.create_customer_in_bulk("
    )

    item_create = normalized.index(
        "self.item_service.create_item_in_bulk("
    )

    ensure_item = normalized.index(
        "self._ensure_item_id(i)"
    )

    document_request = normalized.index(
        "quickbooks_response=(awaitself.client.request("
    )

    assert (
        claim
        < customer_create
        < item_create
        < ensure_item
        < document_request
    )

    assert (
        "provider_started_bids.update("
        in bulk
    )

    assert (
        "provider_request_started"
        not in bulk
    )


def test_quickbooks_bulk_only_prepares_claimed_documents():
    bulk = bulk_source()
    normalized = "".join(bulk.split())

    claim = normalized.index(
        "self.repo.claim_record"
    )

    owned_append = normalized.index(
        "claimed_documents.append(document)",
        claim,
    )

    customer_map = normalized.index(
        "customers_map[key]=document.customer",
        owned_append,
    )

    item_map = normalized.index(
        "items_map[key]=document.items",
        owned_append,
    )

    provider_customer_create = normalized.index(
        "self.customer_service.create_customer_in_bulk(",
        item_map,
    )

    assert (
        claim
        < owned_append
        < customer_map
        < item_map
        < provider_customer_create
    )

    assert (
        "for document in claimed_documents:"
        in bulk
    )

    assert (
        "claimed_invoice_records"
        in bulk
    )


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


def test_quickbooks_preparation_failures_release_early_claim():
    bulk = bulk_source()

    assert (
        "async def release_preparation_claim("
        in bulk
    )

    assert (
        "await release_preparation_claim("
        in bulk
    )

    helper = bulk.index(
        "async def release_preparation_claim("
    )

    release = bulk.index(
        "self.repo.release_record_claim",
        helper,
    )

    document_request = bulk.index(
        "quickbooks_response = (",
        release,
    )

    assert release < document_request


def test_quickbooks_has_only_one_claim_site_in_bulk_owner():
    bulk = bulk_source()

    assert bulk.count(
        "self.repo.claim_record"
    ) == 1
