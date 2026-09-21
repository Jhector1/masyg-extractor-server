from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text()


def test_history_sync_is_inbox_message_added_partial_sync():
    client = read(
        "masyg_extractor/integrations/document_sources/gmail/client.py"
    )
    assert "async def list_gmail_history(" in client
    assert '"startHistoryId"' in client
    assert '"historyTypes": "messageAdded"' in client
    assert '"labelId": "INBOX"' in client
    assert "GmailHistoryExpiredError" in client
    assert "response.status_code == 404" in client


def test_message_and_attachment_reads_use_gmail_readonly_surface():
    client = read(
        "masyg_extractor/integrations/document_sources/gmail/client.py"
    )
    assert 'params={"format": "full"}' in client
    assert "/attachments/{attachment}" in client
    assert "decode_gmail_body_data" in client


def test_processor_reuses_canonical_document_ingestion():
    processor = read(
        "masyg_extractor/integrations/document_sources/gmail/processor.py"
    )
    assert (
        "from masyg_extractor.services.document_ingestion import "
        "ingest_documents"
    ) in processor
    assert "ExtractorProgressLog" in processor
    assert "user_has_active_subscription" in processor
    assert "SUPPORTED_GMAIL_DOCUMENT_EXTENSIONS" in processor


def test_attachment_claim_precedes_ingestion_and_cursor_advances_after_success():
    processor = read(
        "masyg_extractor/integrations/document_sources/gmail/processor.py"
    )

    claim = processor.index("repository.claim_attachment")
    reconcile = processor.index(
        "repository.group_ingestion_succeeded",
        claim,
    )
    ingest = processor.index(
        "result = await ingest_documents(",
        reconcile,
    )
    processed = processor.index(
        "repository.mark_attachment_processed",
        ingest,
    )
    advance = processor.index(
        "repository.advance_history_id",
        processed,
    )

    assert claim < reconcile < ingest < processed < advance


def test_processor_does_not_advance_cursor_on_partial_failure():
    processor = read(
        "masyg_extractor/integrations/document_sources/gmail/processor.py"
    )
    partial = processor.index("if failures:")
    advance = processor.index("repository.advance_history_id")
    assert partial < advance
    assert '"status": "partial"' in processor


def test_history_expiry_fails_closed_for_recovery():
    processor = read(
        "masyg_extractor/integrations/document_sources/gmail/processor.py"
    )
    expired = processor.index("except GmailHistoryExpiredError:")
    recovery = processor.index(
        "repository.mark_sync_recovery_required", expired
    )
    advance = processor.index("repository.advance_history_id")
    assert expired < recovery < advance
    assert '"status": "recovery_required"' in processor


def test_processing_and_attachment_claims_are_transactional():
    repository = read(
        "masyg_extractor/integrations/document_sources/gmail/repository.py"
    )
    assert "def claim_processing_lease(" in repository
    assert "def claim_attachment(" in repository
    assert repository.count("@firestore.transactional") >= 5
    assert '"gmailProcessing"' in repository
    assert '.collection("gmailImports")' in repository


def test_disconnect_cleans_gmail_import_claims():
    repository = read(
        "masyg_extractor/integrations/document_sources/gmail/repository.py"
    )
    disconnect = repository[
        repository.index("def disconnect(self) -> None:"):
    ]
    assert "self._delete_import_claims()" in disconnect
    assert "self.integration_ref().delete()" in disconnect


def test_background_processor_is_durable_scheduler_work():
    server = read("server.py")
    assert "process_pending_gmail_notifications" in server
    assert 'id="gmail_notification_processor"' in server
    assert "IntervalTrigger(seconds=60)" in server
    assert "max_instances=1" in server

def test_gmail_uses_deterministic_group_id_for_canonical_ingestion():
    processor = read(
        "masyg_extractor/integrations/document_sources/gmail/processor.py"
    )
    identity = read(
        "masyg_extractor/integrations/document_sources/gmail/identity.py"
    )
    ingestion = read(
        "masyg_extractor/services/document_ingestion.py"
    )

    assert "def gmail_group_id(" in identity
    assert "gmail_group_id(" in processor
    assert "group_id=group_id" in processor
    assert "group_id: str | None = None" in ingestion
    assert (
        'group_id = str(group_id or "").strip() or generate_group_id()'
        in ingestion
    )


def test_completed_group_is_reconciled_before_attachment_reingestion():
    processor = read(
        "masyg_extractor/integrations/document_sources/gmail/processor.py"
    )
    repository = read(
        "masyg_extractor/integrations/document_sources/gmail/repository.py"
    )

    reconcile = processor.index(
        "repository.group_ingestion_succeeded"
    )
    ingest = processor.index(
        "result = await ingest_documents("
    )

    assert reconcile < ingest
    assert "def group_ingestion_succeeded(" in repository
    assert 'metadata.get("status") == "failed"' in repository
    assert "file_count > 0" in repository

