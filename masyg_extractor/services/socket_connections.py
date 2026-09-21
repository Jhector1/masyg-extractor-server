"""Socket connection ownership primitives.

The browser first receives a random client_id through the signed Starlette session at
/client-id. Socket.IO connections must be bound to that session value; a query-string
clientId is only a consistency check, never an authority for selecting a room.
"""
from __future__ import annotations

import asyncio
from http.cookies import SimpleCookie
import urllib.parse

from jose import JWTError

from masyg_extractor.config.jwt_config import decode_jwt_token


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


def resolve_optional_socket_user_id(scope: dict) -> str | None:
    """Return the authenticated access-token subject for Socket.IO room routing.

    Socket identity remains owned by the signed Starlette session client_id. This helper
    only adds a best-effort authenticated user room for server-originated background
    events such as Gmail imports. Missing, invalid, or expired access cookies do not
    reject the existing socket connection.
    """
    headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in (scope.get("headers") or [])
    }
    raw_cookie = headers.get("cookie", "")
    if not raw_cookie:
        return None

    cookies = SimpleCookie()
    try:
        cookies.load(raw_cookie)
    except Exception:
        return None

    morsel = cookies.get("access_token")
    token = str(morsel.value if morsel else "").strip()
    if not token:
        return None

    try:
        payload = decode_jwt_token(token, expected_type="access")
    except JWTError:
        return None

    user_id = str(payload.get("sub") or "").strip()
    return user_id or None


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

    async def client_id_for_sid(self, sid: str) -> str | None:
        async with self._lock:
            return self._by_sid.get(sid)

    async def current_sid(self, client_id: str) -> str | None:
        async with self._lock:
            return self._by_client.get(client_id)


socket_connections = SocketConnectionRegistry()
