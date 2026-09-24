from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text()


def test_normal_ingestion_keeps_immediate_failure_persistence_default():
    ingestion = read(
        "masyg_extractor/services/document_ingestion.py"
    )

    assert "persist_failure_artifacts: bool = True" in ingestion
    assert "if not persist_failure_artifacts:" in ingestion
    assert "if persist_failure_artifacts:" in ingestion


def test_failed_file_writer_supports_optional_stable_identity():
    source = read(
        "masyg_extractor/services/file_extractor_service.py"
    )

    assert "file_id: str | None = None" in source
    assert 'str(file_id or "").strip()' in source
    assert "sanitize_generate_unique_filename(filename)" in source


def test_gmail_defers_retryable_failure_artifacts():
    processor = read(
        "masyg_extractor/integrations/document_sources/gmail/processor.py"
    )

    start = processor.index("result = await ingest_documents(")
    end = processor.index(
        'if result.get("error"):',
        start,
    )
    call = processor[start:end]

    assert "persist_failure_artifacts=False" in call


def test_gmail_materializes_terminal_failure_artifacts():
    processor = read(
        "masyg_extractor/integrations/document_sources/gmail/processor.py"
    )

    assert (
        processor.count(
            "await persist_failed_ingestion_artifact("
        )
        >= 3
    )

    assert 'stage="gmail_import"' in processor


def test_terminal_artifact_identity_is_idempotent():
    ingestion = read(
        "masyg_extractor/services/document_ingestion.py"
    )

    assert "async def persist_failed_ingestion_artifact(" in ingestion
    assert 'file_id: str = "terminal-failure"' in ingestion
    assert "failed_count" in ingestion
    assert '"failed_files": [failed_id]' in ingestion


def test_other_ingestion_callers_do_not_enable_deferred_mode():
    route = read(
        "masyg_extractor/routes/data_extractor_routes.py"
    )
    drive = read(
        "masyg_extractor/integrations/document_sources/google_drive/router.py"
    )

    assert "persist_failure_artifacts=False" not in route
    assert "persist_failure_artifacts=False" not in drive
