from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text()


def function_source(relative: str, name: str) -> str:
    source = read(relative)
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            lines = source.splitlines()
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    raise AssertionError(f"missing function {name}")


def test_canonical_ingestion_owns_all_extraction_workflow():
    service = read("masyg_extractor/services/document_ingestion.py")
    route_owner = function_source(
        "masyg_extractor/routes/data_extractor_routes.py",
        "extract_data",
    )

    assert "file_buffers = await buffer_upload_files(" in service
    assert "start_index=start_index" in service
    assert "process_files_in_parallel(" in service
    assert "record_failed_file(" in service
    assert "compress_file_blob" in service
    assert "document_set(" in service
    assert "sio.emit(" in service

    assert "await ingest_documents(" in route_owner
    assert "process_files_in_parallel(" not in route_owner
    assert "record_failed_file(" not in route_owner
    assert "compress_file_blob" not in route_owner


def test_drive_import_terminates_at_canonical_ingestion():
    router = read(
        "masyg_extractor/integrations/document_sources/google_drive/router.py"
    )
    service = read("masyg_extractor/services/document_ingestion.py")

    assert '@router.post("/import", status_code=status.HTTP_201_CREATED)' in router
    assert "return await ingest_documents(" in router
    assert "process_files_in_parallel(" not in router
    assert "record_failed_file(" not in router
    assert "compress_file_blob" not in router
    assert "process_files_in_parallel(" in service


def test_drive_import_is_explicit_and_bounded():
    router = read(
        "masyg_extractor/integrations/document_sources/google_drive/router.py"
    )

    assert "validate_import_count(len(file_ids))" in router
    assert "if len(file_ids) > 10:" not in router
    assert "if len(set(file_ids)) != len(file_ids):" in router
    assert "request.session.get(\"client_id\") or \"Guest\"" in router


def test_drive_file_download_is_narrow_and_filters_types():
    client = read(
        "masyg_extractor/integrations/document_sources/google_drive/client.py"
    )

    assert 'GOOGLE_DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"' in client
    assert '"alt": "media"' in client
    assert '"supportsAllDrives": "true"' in client
    assert "capabilities(canDownload)" in client
    assert '"application/pdf"' in client
    assert '"image/jpeg"' in client
    assert '"image/png"' in client
    assert "SUPPORTED_IMPORT_MIME_TYPES" in client


def test_drive_refresh_token_never_leaves_backend_response():
    router = read(
        "masyg_extractor/integrations/document_sources/google_drive/router.py"
    )
    status_source = function_source(
        "masyg_extractor/integrations/document_sources/google_drive/router.py",
        "google_drive_status",
    )
    picker_source = function_source(
        "masyg_extractor/integrations/document_sources/google_drive/router.py",
        "google_drive_picker_token",
    )

    # Passive status never exposes provider credentials.
    assert '"provider": "google_drive"' in status_source
    assert '"connected": connected' in status_source
    assert '"max_import_files": max_import_files()' in status_source
    assert '"access_token":' not in status_source
    assert '"refresh_token":' not in status_source

    # Picker may receive one short-lived access token only after explicit import.
    assert '"access_token": token["access_token"]' in picker_source
    assert '"refresh_token":' not in picker_source

    # No endpoint serializes the refresh token.
    assert '"refresh_token":' not in router
    assert "repository.refresh_token()" in router


def test_drive_repository_uses_existing_token_repository_api():
    repository = read(
        "masyg_extractor/integrations/document_sources/google_drive/repository.py"
    )

    assert "IntegrationTokenRepository(" in repository
    assert "self.tokens.store_integration_token(" in repository
    assert "self.tokens.get_integration_token()" in repository
    assert "db=self.db" not in repository
    assert "Fernet(" not in repository


def test_shared_import_limit_is_runtime_env_authority():
    service = read("masyg_extractor/services/document_ingestion.py")
    route = read("masyg_extractor/routes/data_extractor_routes.py")
    router = read("masyg_extractor/integrations/document_sources/google_drive/router.py")
    assert 'MAX_IMPORT_FILES_ENV = "MASYG_MAX_IMPORT_FILES"' in service
    assert "DEFAULT_MAX_IMPORT_FILES = 200" in service
    assert "validate_import_count(len(files))" in route
    assert "validate_import_count(len(file_ids))" in router
    assert "up to 10 files" not in router
