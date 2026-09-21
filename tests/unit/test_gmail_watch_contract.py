from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text()


def test_watch_uses_gmail_watch_contract_without_ingestion():
    client = read(
        "masyg_extractor/integrations/document_sources/gmail/client.py"
    )

    assert "/users/me/watch" in client
    assert '"topicName": topic' in client
    assert '"labelIds": ["INBOX"]' in client
    assert (
        '"labelFilterBehavior": "INCLUDE"'
        in client
    )
    assert "history_id" in client
    assert "expiration_ms" in client
    assert "history.list" not in client
    assert "ingest_documents" not in client


def test_watch_and_notification_do_not_advance_processed_cursor():
    repository = read(
        "masyg_extractor/integrations/document_sources/gmail/repository.py"
    )

    assert '"gmailWatch"' in repository
    assert '"gmailNotification"' in repository
    assert '"latestHistoryId"' in repository

    record_start = repository.index(
        "def record_notification"
    )
    record_end = repository.index(
        "def notification_state",
        record_start,
    )
    record_block = repository[record_start:record_end]
    assert "gmailHistoryId" not in record_block


def test_push_requires_google_oidc_identity():
    pubsub = read(
        "masyg_extractor/integrations/document_sources/gmail/pubsub.py"
    )

    assert "verify_oauth2_token" in pubsub
    assert "GMAIL_PUBSUB_PUSH_AUDIENCE" in pubsub
    assert (
        "GMAIL_PUBSUB_PUSH_SERVICE_ACCOUNT"
        in pubsub
    )
    assert "email_verified" in pubsub


def test_push_ack_is_after_durable_record():
    router = read(
        "masyg_extractor/integrations/document_sources/gmail/router.py"
    )

    start = router.index(
        "async def gmail_pubsub_push"
    )
    end = router.index(
        '@router.post("/disconnect")',
        start,
    )
    block = router[start:end]

    assert block.index(
        "verify_push_authorization"
    ) < block.index(
        "decode_gmail_notification"
    )
    assert block.index(
        "record_notification"
    ) < block.rindex(
        "Response(status_code=204)"
    )


def test_disconnect_stops_watch_before_google_revocation():
    router = read(
        "masyg_extractor/integrations/document_sources/gmail/router.py"
    )
    start = router.index(
        '@router.post("/disconnect")'
    )
    block = router[start:]

    assert block.index(
        "stop_gmail_watch_for_user"
    ) < block.index(
        "revoke_google_token"
    )


def test_daily_scheduler_renews_gmail_watch():
    server = read("server.py")

    assert "renew_gmail_watches" in server
    assert (
        'id="gmail_watch_renewal_daily"'
        in server
    )


def test_a3c_watch_boundary_stays_separate_from_a3d_ingestion():
    service = read(
        "masyg_extractor/integrations/document_sources/gmail/service.py"
    )
    pubsub = read(
        "masyg_extractor/integrations/document_sources/gmail/pubsub.py"
    )
    processor = read(
        "masyg_extractor/integrations/document_sources/gmail/processor.py"
    )

    assert "ingest_documents" not in service
    assert "ingest_documents" not in pubsub
    assert "ingest_documents" in processor
    assert "list_gmail_history" in processor
    assert "get_gmail_attachment" in processor
