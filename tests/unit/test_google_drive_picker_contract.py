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
            return "\n".join(lines[node.lineno - 1:node.end_lineno])
    raise AssertionError(f"missing function {name}")

def test_picker_token_is_explicit_post_and_refreshes_server_side():
    router = read(
        "masyg_extractor/integrations/document_sources/google_drive/router.py"
    )
    picker = function_source(
        "masyg_extractor/integrations/document_sources/google_drive/router.py",
        "google_drive_picker_token",
    )

    assert '@router.post("/picker-token")' in router
    assert "repository.refresh_token()" in picker
    assert "await refresh_access_token(" in picker
    assert "repository.store_token(" in picker
    assert '"access_token": token["access_token"]' in picker
    assert '"refresh_token":' not in picker

def test_passive_status_exposes_backend_import_limit():
    status = function_source(
        "masyg_extractor/integrations/document_sources/google_drive/router.py",
        "google_drive_status",
    )
    assert '"max_import_files": max_import_files()' in status
    assert "build_authorization_url" not in status

def test_callback_returns_to_drive_management_page():
    router = read(
        "masyg_extractor/integrations/document_sources/google_drive/router.py"
    )
    assert "/data/integration/google-drive?" in router

def test_drive_mime_types_match_canonical_image_pipeline():
    client = read(
        "masyg_extractor/integrations/document_sources/google_drive/client.py"
    )
    assert '"image/bmp"' in client
    assert '"image/webp"' not in client
