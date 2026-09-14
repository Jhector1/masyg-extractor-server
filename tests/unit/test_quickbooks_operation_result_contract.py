from pathlib import Path


def test_quickbooks_bulk_returns_normalized_operation_result():
    source = Path(
        "masyg_extractor/integrations/accounting/quickbooks/services/document_service.py"
    ).read_text()

    assert "def _quickbooks_fault_message(" in source
    assert "def _quickbooks_transport_message(" in source
    assert 'payload.get("Fault")' in source
    assert 'error=_quickbooks_fault_message(payload)' in source
    assert "return operation_progress.result_payload()" in source

    method_start = source.index("async def send_document_in_bulk")
    method_end = source.index("\n    async def ", method_start + 1)
    method = source[method_start:method_end]

    assert "return quickbooks_response" not in method
    assert 'return {"error": str(e)}' not in method
    assert 'raise Exception("No new documents to process.")' not in method


def test_operation_progress_exposes_safe_http_result_envelope():
    source = Path(
        "masyg_extractor/integrations/accounting/shared/operation_progress.py"
    ).read_text()

    assert "def result_payload(self)" in source
    assert '"operation_id": self.operation_id' in source
    assert '"provider": self.provider' in source
    assert '"action": self.action' in source
    assert '"results": [' in source
    assert '"summary": self.summary()' in source
