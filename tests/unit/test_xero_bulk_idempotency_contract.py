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


def test_xero_bulk_claims_before_all_provider_mutations():
    bulk = bulk_source()
    normalized = "".join(
        bulk.split()
    )

    claim = normalized.index(
        "self.repo.claim_record"
    )

    customer_create = normalized.index(
        "self.customer_service.create_customer_in_bulk("
    )

    item_create = normalized.index(
        "self.item_service.create_item_in_bulk("
    )

    document_request = normalized.index(
        "xero_response=awaitself.client.request("
    )

    assert (
        claim
        < customer_create
        < item_create
        < document_request
    )

    assert (
        "provider_started_transaction_ids.update("
        in bulk
    )

    assert (
        "provider_request_started"
        not in bulk
    )


def test_xero_bulk_only_prepares_owned_documents():
    bulk = bulk_source()
    normalized = "".join(
        bulk.split()
    )

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

    customer_create = normalized.index(
        "self.customer_service.create_customer_in_bulk(",
        item_map,
    )

    assert (
        claim
        < owned_append
        < customer_map
        < item_map
        < customer_create
    )

    assert (
        "for document in claimed_documents:"
        in bulk
    )

    assert (
        "claimed_records"
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

def test_xero_bulk_uses_shared_document_chunk_policy():
    bulk = bulk_source()
    normalized = "".join(
        bulk.split()
    )

    assert (
        'chunk_accounting_provider_documents('
        '"xero",document_payload_bulk,)'
        in normalized
    )

    assert (
        '"Invoices":document_chunk'
        in normalized
    )


def test_xero_chunk_alignment_preserves_payload_document_record_positions():
    bulk = bulk_source()
    normalized = "".join(
        bulk.split()
    )

    assert (
        "chunk_documents=(prepared_documents["
        in normalized
    )

    assert (
        "chunk_invoice_records=(invoice_records["
        in normalized
    )

    assert (
        "chunk_offset:chunk_offset+chunk_size"
        in normalized
    )

    assert (
        "len(document_chunk)"
        "==len(chunk_documents)"
        "==len(chunk_invoice_records)"
        in normalized
    )

    service_tree = ast.parse(
        SERVICE.read_text()
    )

    assert any(
        isinstance(node, ast.Constant)
        and node.value
        == "Xero provider chunk alignment was lost."
        for node in ast.walk(service_tree)
    )


def test_xero_provider_errors_are_chunk_relative():
    bulk = bulk_source()
    normalized = "".join(
        bulk.split()
    )

    error_map = normalized.index(
        "provider_error_by_index:"
    )

    chunk_loop = normalized.index(
        "fordocument_chunkindocument_chunks:"
    )

    document_index_loop = normalized.index(
        "for(index,document,)inenumerate("
        "chunk_documents):",
        chunk_loop,
    )

    assert (
        chunk_loop
        < error_map
        < document_index_loop
    )


def test_xero_tracks_provider_start_per_document():
    bulk = bulk_source()

    assert (
        "provider_started_transaction_ids: "
        "set[str] = set()"
        in bulk
    )

    token = bulk.index(
        "xero_token = ("
    )

    started = bulk.index(
        "provider_started_transaction_ids.update(",
        token,
    )

    request = bulk.index(
        "xero_response = await self.client.request(",
        started,
    )

    assert token < started < request


def test_xero_top_level_error_releases_only_unsent_later_chunks():
    bulk = bulk_source()
    normalized = "".join(
        bulk.split()
    )

    top_error = normalized.index(
        'if"error"inxero_response:'
    )

    pending_guard = normalized.index(
        "pending_transaction_id"
        "inprovider_started_transaction_ids",
        top_error,
    )

    release = normalized.index(
        "self.repo.release_record_claim",
        pending_guard,
    )

    assert (
        top_error
        < pending_guard
        < release
    )

    assert (
        "Xero batch stopped "
        in bulk
    )
    assert (
        "before this document "
        in bulk
    )
    assert (
        '"was sent."'
        in bulk
    )


def test_xero_missing_results_are_scoped_to_started_chunk():
    bulk = bulk_source()

    marker = bulk.index(
        "# Resolve only missing results from this exact"
    )

    scoped = bulk[marker:]

    assert (
        "for document in chunk_documents:"
        in scoped
    )

    assert (
        "self.repo.mark_record_uncertain"
        in scoped
    )


def test_xero_exception_cleanup_uses_per_document_provider_start():
    bulk = bulk_source()
    normalized = "".join(
        bulk.split()
    )

    exception = normalized.index(
        "exceptExceptionase:"
    )

    started_guard = normalized.index(
        "if(transaction_id"
        "inprovider_started_transaction_ids):",
        exception,
    )

    uncertain = normalized.index(
        "self.repo.mark_record_uncertain",
        started_guard,
    )

    release = normalized.index(
        "self.repo.release_record_claim",
        uncertain,
    )

    assert (
        started_guard
        < uncertain
        < release
    )


def test_xero_preparation_failures_release_early_claim():
    bulk = bulk_source()

    assert (
        "async def release_preparation_claim("
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
        "xero_response = await self.client.request(",
        release,
    )

    assert release < document_request

    assert (
        "await release_preparation_claim("
        in bulk
    )


def test_xero_has_only_one_claim_site_in_bulk_owner():
    bulk = bulk_source()

    assert bulk.count(
        "self.repo.claim_record"
    ) == 1
