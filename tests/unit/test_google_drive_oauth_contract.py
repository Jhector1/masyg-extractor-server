from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

from masyg_extractor.integrations.document_sources.google_drive.client import (
    GOOGLE_DRIVE_FILE_SCOPE,
    build_authorization_url,
)


ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text()


def test_drive_oauth_requests_only_drive_file_scope(monkeypatch):
    monkeypatch.setenv("GOOGLE_DRIVE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_DRIVE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv(
        "GOOGLE_DRIVE_REDIRECT_URI",
        "http://localhost:5000/integrations/google-drive/callback",
    )

    url = build_authorization_url("opaque-state")
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert parsed.netloc == "accounts.google.com"
    assert query["scope"] == [GOOGLE_DRIVE_FILE_SCOPE]
    assert query["scope"] != ["https://www.googleapis.com/auth/drive"]
    assert query["scope"] != ["https://www.googleapis.com/auth/drive.readonly"]
    assert query["access_type"] == ["offline"]
    assert query["include_granted_scopes"] == ["true"]
    assert query["state"] == ["opaque-state"]


def test_drive_oauth_is_explicit_and_status_is_passive():
    source = read(
        "masyg_extractor/integrations/document_sources/google_drive/router.py"
    )
    assert '@router.post("/connect")' in source
    assert '@router.get("/connect")' not in source
    assert '@router.get("/status")' in source
    assert '@router.post("/disconnect")' in source
    assert "build_authorization_url(state)" in source
    assert "GoogleDriveCredentialRepository(user_id).is_connected()" in source


def test_drive_credentials_reuse_hardened_encrypted_token_owner():
    source = read(
        "masyg_extractor/integrations/document_sources/google_drive/repository.py"
    )
    assert "IntegrationTokenRepository" in source
    assert 'GOOGLE_DRIVE_INTEGRATION_ID = "google_drive"' in source
    assert "self.tokens.store_integration_token(" in source
    assert "self.tokens.get_integration_token()" in source
    assert ".delete()" in source
    assert "Fernet(" not in source


def test_drive_callback_never_uses_masyg_google_signin_or_vision_credentials():
    source = read(
        "masyg_extractor/integrations/document_sources/google_drive/router.py"
    )
    client = read(
        "masyg_extractor/integrations/document_sources/google_drive/client.py"
    )
    assert "googleIdToken" not in source + client
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in source + client
    assert "read_google_drive_state(state_value)" in source


def test_drive_state_is_ttl_and_purpose_bound():
    source = read(
        "masyg_extractor/integrations/document_sources/google_drive/oauth_state.py"
    )
    assert "STATE_TTL_SECONDS = 10 * 60" in source
    assert '_STATE_PURPOSE = "google_drive_oauth"' in source
    assert "ttl=STATE_TTL_SECONDS" in source
    assert 'payload.get("purpose") != _STATE_PURPOSE' in source


def test_drive_router_is_registered():
    source = read("masyg_extractor/routes/__init__.py")
    assert (
        "from masyg_extractor.integrations.document_sources.google_drive.router "
        "import router as google_drive_router"
    ) in source
    assert 'app.include_router(google_drive_router, prefix="")' in source


def test_drive_provider_does_not_own_extraction_pipeline():
    root = ROOT / "masyg_extractor/integrations/document_sources/google_drive"
    source = "\n".join(path.read_text() for path in root.rglob("*.py"))
    assert "process_files_in_parallel(" not in source
    assert "/extract-data" not in source
