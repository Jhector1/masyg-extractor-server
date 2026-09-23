from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text()


def test_claim_lifecycle_is_bounded():
    repository = read(
        "masyg_extractor/integrations/document_sources/gmail/repository.py"
    )

    assert 'status in {"terminal", "dead_letter"}' in repository
    assert 'return "retry_later"' in repository
    assert "def mark_attachment_retryable(" in repository
    assert "def mark_attachment_terminal(" in repository
    assert '"nextRetryAt"' in repository
    assert '"terminalReason"' in repository


def test_terminal_failure_does_not_pin_cursor():
    processor = read(
        "masyg_extractor/integrations/document_sources/gmail/processor.py"
    )

    blocker = processor.index(
        "if retryable_failures:"
    )

    advance = processor.index(
        "repository.advance_history_id"
    )

    assert blocker < advance

    assert "if terminal_failures:" not in processor[
        blocker:advance
    ]


def test_ingestion_preserves_failure_details():
    ingestion = read(
        "masyg_extractor/services/document_ingestion.py"
    )

    assert "failure_details" in ingestion
    assert '"error": error_message' in ingestion
    assert '"stage": failure_stage' in ingestion
    assert '"failures": failure_details' in ingestion


def test_message_fetch_failure_has_persisted_bounded_retry():
    repository = read(
        "masyg_extractor/integrations/document_sources/gmail/repository.py"
    )

    processor = read(
        "masyg_extractor/integrations/document_sources/gmail/processor.py"
    )

    assert "def message_fetch_retry_state(" in repository
    assert "def mark_message_fetch_retryable(" in repository
    assert "def clear_message_fetch_retry(" in repository

    assert '"message-fetch"' in repository
    assert '"message_fetch_retry_exhausted"' in repository
    assert '"attemptCount"' in repository
    assert '"nextRetryAt"' in repository

    state = processor.index(
        "repository.message_fetch_retry_state"
    )

    fetch = processor.index(
        "message = await get_gmail_message(",
        state,
    )

    failure = processor.index(
        "repository.mark_message_fetch_retryable",
        fetch,
    )

    assert state < fetch < failure

    assert (
        'message_retry_state == "retry_later"'
        in processor
    )

    assert (
        'message_retry_state == "terminal"'
        in processor
    )


def test_successful_message_fetch_clears_expired_retry_state():
    processor = read(
        "masyg_extractor/integrations/document_sources/gmail/processor.py"
    )

    fetch = processor.index(
        "message = await get_gmail_message("
    )

    clear = processor.index(
        "repository.clear_message_fetch_retry",
        fetch,
    )

    payload = processor.index(
        'payload = message.get("payload") or {}',
        clear,
    )

    assert fetch < clear < payload


def test_message_fetch_retry_uses_import_claim_collection_owner():
    repository = read(
        "masyg_extractor/integrations/document_sources/gmail/repository.py"
    )

    method = repository[
        repository.index(
            "def _message_fetch_retry_ref("
        ):
        repository.index(
            "def message_fetch_retry_state("
        )
    ]

    assert "self._import_claim_ref(" in method
    assert 'part_key="message-fetch"' in method
