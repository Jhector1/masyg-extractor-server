import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def source(relative: str) -> str:
    return (ROOT / relative).read_text()


def test_xero_http_errors_preserve_safe_validation_details():
    client = source("masyg_extractor/integrations/accounting/xero/client.py")

    assert "except httpx.HTTPStatusError as exc:" in client
    assert "document_errors" in client
    assert "ValidationErrors" in client
    assert "status_code" in client
    assert "response.raise_for_status()" in client
    assert '"raw_response"' not in client


def test_xero_bulk_returns_canonical_operation_result():
    service = source(
        "masyg_extractor/integrations/accounting/xero/services/document_service.py"
    )

    tree = ast.parse(service)
    document_service = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "DocumentService"
    )
    bulk_method = next(
        node
        for node in document_service.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "send_document_in_bulk"
    )
    lines = service.splitlines()
    bulk = "\n".join(lines[bulk_method.lineno - 1 : bulk_method.end_lineno])

    assert bulk.count("return operation_progress.result_payload()") >= 4
    assert 'return {"error": str(e)}' not in bulk
    assert 'return {"error": "No valid documents processed."}' not in bulk


def test_xero_bulk_does_not_claim_success_before_provider_result():
    service = source(
        "masyg_extractor/integrations/accounting/xero/services/document_service.py"
    )

    request_pos = service.index("xero_response = await self.client.request(")
    premature = "processed for {document.customer.name} successfully"
    confirmed = "created in Xero for "

    assert premature not in service

    confirmed_pos = service.index(confirmed)
    assert confirmed_pos > request_pos


def test_xero_bulk_maps_provider_errors_to_documents():
    service = source(
        "masyg_extractor/integrations/accounting/xero/services/document_service.py"
    )

    assert "document_errors" in service
    assert "_xero_response_invoice_error" in service
    assert "provider_error_by_index" in service
    assert "Xero rejected this document." in service


def test_xero_bulk_finalizes_confirmed_successes_and_uses_xero_identity():
    service = source(
        "masyg_extractor/integrations/accounting/xero/services/document_service.py"
    )

    assert '"integration": "xero"' in service
    assert "self.repo.finalize_record" in service
    assert '"providerDocumentId"' in service


def test_xero_bulk_does_not_dump_full_documents_to_stdout():
    service = source(
        "masyg_extractor/integrations/accounting/xero/services/document_service.py"
    )

    assert "print(len(documents), documents)" not in service
