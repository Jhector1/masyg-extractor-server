from masyg_extractor.integrations.document_sources.gmail.retry_policy import (
    classify_ingestion_failure,
    retry_delay_seconds,
)


def test_unsupported_document_type_is_terminal():
    outcome, error, stage = classify_ingestion_failure({
        "error": "Files Processing Failed",
        "failures": [{
            "error": "Unsupported document type",
            "stage": "Document classification",
        }],
    })

    assert outcome == "terminal"
    assert error == "Unsupported document type"
    assert stage == "Document classification"


def test_empty_file_is_terminal():
    outcome, error, _ = classify_ingestion_failure({
        "failures": [{
            "error": "Empty file",
            "stage": "parsing",
        }],
    })

    assert outcome == "terminal"
    assert error == "Empty file"


def test_text_extraction_failure_is_retryable():
    outcome, error, _ = classify_ingestion_failure({
        "failures": [{
            "error": "Text extraction failed",
            "stage": "text_extraction",
        }],
    })

    assert outcome == "retryable"
    assert error == "Text extraction failed"


def test_unknown_failure_is_retryable():
    outcome, _, _ = classify_ingestion_failure({
        "failures": [{
            "error": "provider temporarily unavailable",
            "stage": "File task failed",
        }],
    })

    assert outcome == "retryable"


def test_retry_backoff_is_bounded():
    assert retry_delay_seconds(1) == 300
    assert retry_delay_seconds(2) == 600
    assert retry_delay_seconds(3) == 1200
    assert retry_delay_seconds(4) == 2400
    assert retry_delay_seconds(10) == 3600
