"""Socket connection ownership primitives.

The browser first receives a random client_id through the signed Starlette session at
/client-id. Socket.IO connections must be bound to that session value; a query-string
clientId is only a consistency check, never an authority for selecting a room.
"""
from __future__ import annotations

import asyncio
import urllib.parse


class SocketIdentityError(ValueError):
    pass


def resolve_session_client_id(scope: dict, auth: dict | None = None) -> str:
    session = scope.get("session") or {}
    session_client_id = str(session.get("client_id") or "").strip()
    if not session_client_id:
        raise SocketIdentityError("Missing signed session client_id")

    query_string = (scope.get("query_string") or b"").decode("utf-8", errors="replace")
    query = urllib.parse.parse_qs(query_string)
    requested = str((auth or {}).get("client_id") or query.get("clientId", [""])[0]).strip()
    if requested and requested != session_client_id:
        raise SocketIdentityError("Socket client_id does not match signed session")

    return session_client_id


class SocketConnectionRegistry:
    """One authoritative SID per client_id within this application process.

    Redis-backed Socket.IO still distributes room messages across workers. This registry
    prevents duplicate ownership in the process that observes the reconnect, which is the
    duplicate pattern seen in development and single-worker production deployments.
    """

    def __init__(self) -> None:
        self._by_client: dict[str, str] = {}
        self._by_sid: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def claim(self, client_id: str, sid: str) -> str | None:
        async with self._lock:
            previous_sid = self._by_client.get(client_id)
            old_client = self._by_sid.get(sid)
            if old_client and old_client != client_id:
                if self._by_client.get(old_client) == sid:
                    self._by_client.pop(old_client, None)
            self._by_client[client_id] = sid
            self._by_sid[sid] = client_id
            return previous_sid if previous_sid != sid else None

    async def release(self, sid: str) -> str | None:
        async with self._lock:
            client_id = self._by_sid.pop(sid, None)
            if client_id and self._by_client.get(client_id) == sid:
                self._by_client.pop(client_id, None)
            return client_id

    async def current_sid(self, client_id: str) -> str | None:
        async with self._lock:
            return self._by_client.get(client_id)


socket_connections = SocketConnectionRegistry()
