from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text()


def test_socket_connect_joins_authenticated_user_room_without_replacing_client_room():
    server = read("server.py")
    sockets = read("masyg_extractor/services/socket_connections.py")

    assert "resolve_session_client_id(scope, auth)" in server
    assert "resolve_optional_socket_user_id(scope)" in server
    assert "await sio.enter_room(sid, client_id)" in server
    assert 'await sio.enter_room(sid, f"user:{user_id}")' in server

    assert 'cookies.get("access_token")' in sockets
    assert 'decode_jwt_token(token, expected_type="access")' in sockets


def test_gmail_success_uses_durable_notification_owner_after_processed_mark():
    processor = read(
        "masyg_extractor/integrations/document_sources/gmail/processor.py"
    )

    processed = processor.index("repository.mark_attachment_processed")
    imported = processor.index("imported += 1", processed)
    notify = processor.index("await publish_user_notification(", imported)

    assert processed < imported < notify
    assert 'type="document.imported"' in processor
    assert 'source="gmail"' in processor
    assert 'dedupe_key=f"gmail:document.imported:{group_id}"' in processor


def test_gmail_failure_alert_is_deduped_without_attachment_id():
    processor = read(
        "masyg_extractor/integrations/document_sources/gmail/processor.py"
    )

    start = processor.index('"gmail:document.import_failed:"')
    window = processor[start : start + 300]

    assert 'type="document.import_failed"' in processor
    assert 'f"{message_id}:{filename.lower()}"' in window
    assert "part_key" not in window
