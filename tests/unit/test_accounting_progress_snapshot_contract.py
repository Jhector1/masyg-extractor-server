import asyncio
from pathlib import Path

from masyg_extractor.services.socket_connections import SocketConnectionRegistry


def test_socket_registry_resolves_client_from_sid():
    registry = SocketConnectionRegistry()

    async def run_test():
        await registry.claim("client-1", "sid-1")
        assert await registry.client_id_for_sid("sid-1") == "client-1"
        await registry.release("sid-1")
        assert await registry.client_id_for_sid("sid-1") is None

    asyncio.run(run_test())


def test_server_replays_accounting_snapshot_from_snapshot_event():
    source = Path("server.py").read_text()
    assert '@sio.on("progress_request_snapshot")' in source
    assert "await socket_connections.client_id_for_sid(sid)" in source
    assert "await emit_accounting_operation_snapshot(" in source
    assert "to_sid=sid" in source
