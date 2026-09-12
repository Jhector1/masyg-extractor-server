import asyncio

import pytest

from masyg_extractor.services.socket_connections import (
    SocketConnectionRegistry,
    SocketIdentityError,
    resolve_session_client_id,
)


def test_socket_identity_is_bound_to_signed_session():
    scope = {
        "session": {"client_id": "client-123"},
        "query_string": b"clientId=client-123",
    }
    assert resolve_session_client_id(scope, None) == "client-123"


def test_socket_identity_rejects_room_spoofing():
    scope = {
        "session": {"client_id": "client-real"},
        "query_string": b"clientId=client-other",
    }
    with pytest.raises(SocketIdentityError, match="does not match"):
        resolve_session_client_id(scope, None)


def test_socket_identity_requires_session_owner():
    scope = {"session": {}, "query_string": b"clientId=attacker-selected"}
    with pytest.raises(SocketIdentityError, match="Missing signed session"):
        resolve_session_client_id(scope, None)


def test_socket_registry_replaces_duplicate_without_old_disconnect_erasing_new_owner():
    async def scenario():
        registry = SocketConnectionRegistry()
        assert await registry.claim("client-1", "sid-old") is None
        assert await registry.claim("client-1", "sid-new") == "sid-old"
        assert await registry.current_sid("client-1") == "sid-new"

        # The stale socket's disconnect arrives after the replacement. It must not
        # delete the new authoritative mapping.
        assert await registry.release("sid-old") == "client-1"
        assert await registry.current_sid("client-1") == "sid-new"

        assert await registry.release("sid-new") == "client-1"
        assert await registry.current_sid("client-1") is None

    asyncio.run(scenario())
