from __future__ import annotations

from pathlib import Path

from masyg_extractor.services.progress_log import ExtractorProgressLog


ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text()


def test_chunk_size_is_shared_runtime_env_policy():
    service = read("masyg_extractor/services/document_ingestion.py")
    assert 'IMPORT_CHUNK_SIZE_ENV = "MASYG_IMPORT_CHUNK_SIZE"' in service
    assert "DEFAULT_IMPORT_CHUNK_SIZE = 10" in service
    assert "def import_chunk_size()" in service
    assert "for start_index in range(0, total_files, chunk_size):" in service
    assert "start_index=start_index" in service


def test_chunking_keeps_one_group_and_one_result_owner():
    service = read("masyg_extractor/services/document_ingestion.py")
    assert service.count("generate_group_id()") == 1
    assert (
        'group_id = str(group_id or "").strip() or generate_group_id()'
        in service
    )
    assert "results.update(chunk_results)" in service
    assert "emit_final_overall=False" in service
    assert 'group_obj["group_id"] = group_id' in service


def test_parallel_processor_can_suppress_chunk_level_100():
    processing = read("masyg_extractor/services/processing.py")
    assert "emit_final_overall: bool = True" in processing
    assert "if emit_final_overall:" in processing
    assert "await progress_logger.emit(100.0, file_id=None)" in processing


def test_progress_logger_uses_expected_total_denominator():
    progress = ExtractorProgressLog("test-room")
    progress.clear()
    progress.set_expected_file_count(200)
    progress._registry["one"] = 100.0
    assert progress._registry_overall() == 0.5


def test_drive_documents_are_lazy_not_pre_downloaded():
    router = read(
        "masyg_extractor/integrations/document_sources/google_drive/router.py"
    )
    assert "class GoogleDriveReadableUpload:" in router
    assert "await self.session.content(self.file_id)" in router
    assert "async def _prepare_selected_files(" in router
    assert "metadata = await session.metadata(file_id)" in router
    assert "_download_selected_files" not in router


def test_drive_provider_still_does_not_own_extraction():
    client = read(
        "masyg_extractor/integrations/document_sources/google_drive/client.py"
    )
    router = read(
        "masyg_extractor/integrations/document_sources/google_drive/router.py"
    )
    assert "async def download_drive_file_content(" in client
    assert '"alt": "media"' in client
    assert "process_files_in_parallel(" not in router
