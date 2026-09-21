from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from masyg_extractor.integrations.document_sources.gmail.client import (
    GMAIL_READONLY_SCOPE,
    build_authorization_url,
)


ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text()


def test_gmail_authorization_is_separate_and_read_only(
    monkeypatch,
):
    monkeypatch.setenv(
        "GOOGLE_GMAIL_CLIENT_ID",
        "gmail-client",
    )
    monkeypatch.setenv(
        "GOOGLE_GMAIL_CLIENT_SECRET",
        "gmail-secret",
    )
    monkeypatch.setenv(
        "GOOGLE_GMAIL_REDIRECT_URI",
        "https://server.example/integrations/gmail/callback",
    )

    url = build_authorization_url("state-token")
    query = parse_qs(urlparse(url).query)

    assert query["client_id"] == [
        "gmail-client"
    ]
    assert query["scope"] == [
        GMAIL_READONLY_SCOPE
    ]
    assert query["access_type"] == [
        "offline"
    ]
    assert query["prompt"] == ["consent"]
    assert query["state"] == [
        "state-token"
    ]
    assert (
        "https://www.googleapis.com/auth/drive.file"
        not in query["scope"]
    )


def test_gmail_uses_its_own_environment_contract():
    source = read(
        "masyg_extractor/integrations/document_sources/gmail/client.py"
    )

    assert "GOOGLE_GMAIL_CLIENT_ID" in source
    assert "GOOGLE_GMAIL_CLIENT_SECRET" in source
    assert "GOOGLE_GMAIL_REDIRECT_URI" in source
    assert "GOOGLE_DRIVE_CLIENT_ID" not in source
    assert "gmail.readonly" in source


def test_gmail_reuses_canonical_encrypted_integration_token_owner():
    source = read(
        "masyg_extractor/integrations/document_sources/gmail/repository.py"
    )

    assert "IntegrationTokenRepository" in source
    assert 'GMAIL_INTEGRATION_ID = "gmail"' in source

    # Runtime reads use the canonical decrypted token-map shape, but Gmail must
    # not introduce a second encryption format or directly persist plaintext
    # provider secrets in this repository.
    assert "store_integration_token(" in source
    assert "encryptedAccessToken" not in source
    assert "encryptedRefreshToken" not in source
    assert "tokenData" not in source
    assert "Fernet(" not in source
    assert ".set({" not in source
    assert ".update({" not in source


def test_browser_routes_never_return_provider_tokens():
    source = read(
        "masyg_extractor/integrations/document_sources/gmail/router.py"
    )

    status_block = source[
        source.index('@router.get("/status")'):
        source.index('@router.post("/connect")')
    ]
    connect_block = source[
        source.index('@router.post("/connect")'):
        source.index('@router.get("/callback")')
    ]
    disconnect_block = source[
        source.index('@router.post("/disconnect")'):
    ]

    assert "access_token" not in status_block
    assert "refresh_token" not in status_block
    assert "access_token" not in connect_block
    assert "refresh_token" not in connect_block
    assert '"authorization_url"' in connect_block
    assert '"access_token"' not in disconnect_block


def test_connect_status_disconnect_use_cookie_auth_but_callback_does_not():
    source = read(
        "masyg_extractor/integrations/document_sources/gmail/router.py"
    )

    status_block = source[
        source.index('@router.get("/status")'):
        source.index('@router.post("/connect")')
    ]
    connect_block = source[
        source.index('@router.post("/connect")'):
        source.index('@router.get("/callback")')
    ]
    callback_block = source[
        source.index('@router.get("/callback")'):
        source.index('@router.post("/disconnect")')
    ]
    disconnect_block = source[
        source.index('@router.post("/disconnect")'):
    ]

    assert "get_current_user_from_cookie" in status_block
    assert "get_current_user_from_cookie" in connect_block
    assert "get_current_user_from_cookie" not in callback_block
    assert "read_gmail_state" in callback_block
    assert "get_current_user_from_cookie" in disconnect_block


def test_callback_binds_google_profile_before_persisting_connection():
    source = read(
        "masyg_extractor/integrations/document_sources/gmail/router.py"
    )

    callback = source[
        source.index('@router.get("/callback")'):
        source.index('@router.post("/disconnect")')
    ]

    assert callback.index(
        "get_gmail_profile"
    ) < callback.index(
        "repository.store_token"
    )
    assert '"email_address"' in callback
    assert '"history_id"' in callback


def test_callback_and_disconnect_keep_firestore_calls_off_event_loop():
    source = read(
        "masyg_extractor/integrations/document_sources/gmail/router.py"
    )

    callback = source[
        source.index('@router.get("/callback")'):
        source.index('@router.post("/pubsub")')
    ]
    disconnect = source[
        source.index('@router.post("/disconnect")'):
    ]

    assert "await asyncio.to_thread(" in callback
    assert "repository.store_token" in callback
    assert "repository.claim_mailbox_owner" in callback

    assert "await asyncio.to_thread(" in disconnect
    assert "repository.disconnect" in disconnect


def test_disconnect_revokes_google_before_local_delete():
    source = read(
        "masyg_extractor/integrations/document_sources/gmail/router.py"
    )
    disconnect = source[
        source.index('@router.post("/disconnect")'):
    ]

    assert disconnect.index(
        "revoke_google_token"
    ) < disconnect.index(
        "repository.disconnect"
    )
    assert "HTTP_502_BAD_GATEWAY" in disconnect


def test_no_push_or_mailbox_processing_is_added_in_oauth_foundation():
    root = (
        ROOT
        / "masyg_extractor/integrations/document_sources/gmail"
    )
    combined = "\n".join(
        path.read_text()
        for path in root.glob("*.py")
    )

    assert "history.list" not in combined
    assert "attachments.get" not in combined
    assert "ingest_documents" not in combined
