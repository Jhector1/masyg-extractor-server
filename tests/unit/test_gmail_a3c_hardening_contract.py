from __future__ import annotations

import logging
from pathlib import Path

from masyg_extractor.utils.access_log_redaction import (
    SensitiveOAuthAccessLogFilter,
)


ROOT = Path(__file__).resolve().parents[2]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text()


def test_gmail_mailbox_claim_is_transactional_and_release_is_transactional():
    source = _source(
        "masyg_extractor/integrations/document_sources/gmail/repository.py"
    )

    assert "def claim_mailbox_owner(" in source
    assert "snapshot = ref.get(transaction=txn)" in source
    assert "txn.set(" in source
    assert "GmailMailboxOwnershipError" in source
    assert "txn.delete(ref)" in source


def test_gmail_callback_claims_before_token_persistence_and_rolls_back_new_claim():
    source = _source(
        "masyg_extractor/integrations/document_sources/gmail/router.py"
    )

    claim = source.index("repository.claim_mailbox_owner")
    store = source.index("repository.store_token")
    rollback = source.index("if mailbox_claim_created:")

    assert claim < store < rollback
    assert "repository.release_mailbox_owner" in source


def test_account_deletion_removes_gmail_local_state_before_parent_user():
    source = _source("masyg_extractor/routes/user_routes.py")

    gmail_disconnect = source.index(
        "GmailCredentialRepository(user_id).disconnect"
    )
    parent_delete = source.index("await document_delete(user_ref)")

    assert gmail_disconnect < parent_delete
    assert "stop_gmail_watch_for_user" in source


def test_uvicorn_access_filter_redacts_gmail_callback_query():
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=(
            "127.0.0.1:1234",
            "GET",
            "/integrations/gmail/callback?state=STATE_SECRET&code=CODE_SECRET",
            "1.1",
            303,
        ),
        exc_info=None,
    )

    assert SensitiveOAuthAccessLogFilter().filter(record) is True
    rendered = record.getMessage()

    assert "STATE_SECRET" not in rendered
    assert "CODE_SECRET" not in rendered
    assert "/integrations/gmail/callback?[REDACTED]" in rendered


def test_uvicorn_access_filter_does_not_change_normal_paths():
    original = "/integrations/gmail/status?detail=1"
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=(
            "127.0.0.1:1234",
            "GET",
            original,
            "1.1",
            200,
        ),
        exc_info=None,
    )

    assert SensitiveOAuthAccessLogFilter().filter(record) is True
    assert original in record.getMessage()


def test_server_installs_sensitive_oauth_access_log_filter():
    source = _source("server.py")

    assert "install_sensitive_oauth_access_log_filter" in source
    assert "install_sensitive_oauth_access_log_filter()" in source


def test_gmail_callback_binds_profile_email_before_atomic_claim():
    source = _source(
        "masyg_extractor/integrations/document_sources/gmail/router.py"
    )

    callback = source[
        source.index('@router.get("/callback")'):
        source.index('@router.post("/pubsub")')
    ]

    binding = callback.index("email_address =")
    claim = callback.index("repository.claim_mailbox_owner")

    assert binding < claim
