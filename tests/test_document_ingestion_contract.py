from __future__ import annotations

import asyncio
from pathlib import Path

from masyg_extractor.services.document_ingestion import buffer_upload_files


ROOT = Path(__file__).resolve().parents[1]


class FakeUpload:
    def __init__(self, filename, content):
        self.filename = filename
        self.content = content
        self.position = 0
        self.read_count = 0

    async def read(self):
        self.read_count += 1
        self.position = len(self.content)
        return self.content

    async def seek(self, offset):
        self.position = offset


def test_buffer_upload_files_reads_once_and_rewinds():
    first = FakeUpload("a.pdf", b"a")
    second = FakeUpload("b.pdf", b"b")

    result = asyncio.run(buffer_upload_files([first, second]))

    assert result == [(0, first, b"a"), (1, second, b"b")]
    assert (
        first.read_count,
        second.read_count,
        first.position,
        second.position,
    ) == (1, 1, 0, 0)


def test_extract_route_delegates_to_canonical_ingestion():
    route = (ROOT / "masyg_extractor/routes/data_extractor_routes.py").read_text()
    service = (ROOT / "masyg_extractor/services/document_ingestion.py").read_text()

    assert "await ingest_documents(" in _extract_data_function(route)
    assert "process_files_in_parallel(" not in _extract_data_function(route)
    assert "await process_files_in_parallel(" in service
    assert "file_buffers = await buffer_upload_files(" in service
    assert "start_index=start_index" in service
    assert "record_failed_file(" in service
    assert "compress_file_blob" in service
    assert 'group_obj["group_id"] = group_id' in service


def _extract_data_function(source: str) -> str:
    start = source.index("async def extract_data(")
    end = source.index('@router.post("/update-change-log")', start)
    return source[start:end]


def test_drive_adapter_must_not_own_extraction_pipeline():
    root = ROOT / "masyg_extractor/integrations/document_sources/google_drive"
    if not root.exists():
        return

    source = "\n".join(path.read_text() for path in root.rglob("*.py"))
    assert "process_files_in_parallel(" not in source
    assert "record_failed_file(" not in source
    assert "compress_file_blob" not in source
