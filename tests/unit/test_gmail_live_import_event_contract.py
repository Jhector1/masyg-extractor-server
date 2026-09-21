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
    assert "except JWTError:" in sockets


def test_gmail_success_emits_user_scoped_document_import_event_after_processed_mark():
    processor = read(
        "masyg_extractor/integrations/document_sources/gmail/processor.py"
    )

    processed = processor.index("repository.mark_attachment_processed")
    imported = processor.index("imported += 1", processed)
    emitted = processor.index("GMAIL_DOCUMENT_IMPORTED_EVENT", imported)
    room = processor.index('room=f"user:{normalized_user}"', emitted)

    assert processed < imported < emitted < room
    assert '"source": "gmail"' in processor
    assert '"groupId": group_id' in processor
    assert '"filename": filename' in processor
    assert "Gmail import notification emit failed" in processor
