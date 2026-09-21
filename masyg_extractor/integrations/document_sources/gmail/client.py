from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx


GOOGLE_AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOCATION_URL = "https://oauth2.googleapis.com/revoke"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


class GmailConfigurationError(RuntimeError):
    pass


class GmailOAuthError(RuntimeError):
    pass


class GmailUnauthorizedError(GmailOAuthError):
    pass


@dataclass(frozen=True)
class GmailOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str


def oauth_config() -> GmailOAuthConfig:
    client_id = (os.getenv("GOOGLE_GMAIL_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("GOOGLE_GMAIL_CLIENT_SECRET") or "").strip()
    redirect_uri = (os.getenv("GOOGLE_GMAIL_REDIRECT_URI") or "").strip()

    missing = [
        name
        for name, value in (
            ("GOOGLE_GMAIL_CLIENT_ID", client_id),
            ("GOOGLE_GMAIL_CLIENT_SECRET", client_secret),
            ("GOOGLE_GMAIL_REDIRECT_URI", redirect_uri),
        )
        if not value
    ]
    if missing:
        raise GmailConfigurationError(
            "Missing Gmail OAuth configuration: " + ", ".join(missing)
        )

    return GmailOAuthConfig(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
    )


def build_authorization_url(state: str) -> str:
    normalized_state = str(state or "").strip()
    if not normalized_state:
        raise ValueError("state is required")

    config = oauth_config()
    query = urlencode(
        {
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "response_type": "code",
            "scope": GMAIL_READONLY_SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": normalized_state,
        }
    )
    return f"{GOOGLE_AUTHORIZATION_URL}?{query}"


def _token_scope(payload: dict) -> str:
    scope = str(payload.get("scope") or "").strip()
    granted = set(scope.split())
    if GMAIL_READONLY_SCOPE not in granted:
        raise GmailOAuthError(
            "Google did not grant the required Gmail read-only scope"
        )
    return scope


async def exchange_authorization_code(code: str) -> dict:
    normalized = str(code or "").strip()
    if not normalized:
        raise GmailOAuthError("Authorization code is required")

    config = oauth_config()
    async with httpx.AsyncClient(timeout=20.0) as http:
        response = await http.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "code": normalized,
                "grant_type": "authorization_code",
                "redirect_uri": config.redirect_uri,
            },
            headers={"Accept": "application/json"},
        )

    if response.is_error:
        raise GmailOAuthError("Gmail authorization failed")

    try:
        payload = response.json()
        access_token = str(payload.get("access_token") or "").strip()
        refresh_token = str(payload.get("refresh_token") or "").strip()
        expires_in = int(payload.get("expires_in"))
    except (ValueError, TypeError) as exc:
        raise GmailOAuthError(
            "Google returned an invalid Gmail token response"
        ) from exc

    scope = _token_scope(payload)

    if not access_token or not refresh_token or expires_in <= 0:
        raise GmailOAuthError(
            "Google did not return durable Gmail authorization"
        )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": expires_in,
        "scope": scope,
        "token_type": str(payload.get("token_type") or "Bearer"),
    }


async def refresh_access_token(refresh_token: str) -> dict:
    normalized = str(refresh_token or "").strip()
    if not normalized:
        raise GmailOAuthError("Gmail refresh token is missing")

    config = oauth_config()
    async with httpx.AsyncClient(timeout=20.0) as http:
        response = await http.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "refresh_token": normalized,
                "grant_type": "refresh_token",
            },
            headers={"Accept": "application/json"},
        )

    if response.is_error:
        raise GmailOAuthError("Gmail token refresh failed")

    try:
        payload = response.json()
        access_token = str(payload.get("access_token") or "").strip()
        expires_in = int(payload.get("expires_in"))
    except (ValueError, TypeError) as exc:
        raise GmailOAuthError(
            "Google returned an invalid Gmail refresh response"
        ) from exc

    if not access_token or expires_in <= 0:
        raise GmailOAuthError("Gmail token refresh was incomplete")

    return {
        "access_token": access_token,
        "expires_in": expires_in,
        "token_type": str(payload.get("token_type") or "Bearer"),
    }


def _auth_headers(access_token: str) -> dict[str, str]:
    normalized = str(access_token or "").strip()
    if not normalized:
        raise GmailUnauthorizedError("Gmail access token is missing")
    return {
        "Authorization": f"Bearer {normalized}",
        "Accept": "application/json",
    }


async def get_gmail_profile(access_token: str) -> dict:
    url = f"{GMAIL_API_BASE}/users/me/profile"

    async with httpx.AsyncClient(timeout=20.0) as http:
        response = await http.get(
            url,
            headers=_auth_headers(access_token),
        )

    if response.status_code == 401:
        raise GmailUnauthorizedError("Gmail access token expired")
    if response.is_error:
        raise GmailOAuthError("Unable to read Gmail account profile")

    try:
        payload = response.json()
    except ValueError as exc:
        raise GmailOAuthError(
            "Google returned an invalid Gmail profile response"
        ) from exc

    email_address = str(payload.get("emailAddress") or "").strip().lower()
    history_id = str(payload.get("historyId") or "").strip()

    if not email_address:
        raise GmailOAuthError("Gmail account profile has no email address")

    return {
        "email_address": email_address,
        "history_id": history_id,
    }


async def revoke_google_token(token: str) -> None:
    normalized = str(token or "").strip()
    if not normalized:
        return

    async with httpx.AsyncClient(timeout=20.0) as http:
        response = await http.post(
            GOOGLE_REVOCATION_URL,
            data={"token": normalized},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )

    if response.is_error:
        raise GmailOAuthError("Google Gmail authorization revocation failed")


def gmail_pubsub_topic() -> str:
    topic = (os.getenv("GOOGLE_GMAIL_PUBSUB_TOPIC") or "").strip()
    if not topic:
        raise GmailConfigurationError(
            "GOOGLE_GMAIL_PUBSUB_TOPIC is required"
        )

    parts = topic.split("/")
    if (
        len(parts) != 4
        or parts[0] != "projects"
        or not parts[1]
        or parts[2] != "topics"
        or not parts[3]
    ):
        raise GmailConfigurationError(
            "GOOGLE_GMAIL_PUBSUB_TOPIC must use "
            "projects/<project>/topics/<topic>"
        )

    return topic


async def start_gmail_watch(access_token: str) -> dict:
    topic = gmail_pubsub_topic()
    url = f"{GMAIL_API_BASE}/users/me/watch"

    async with httpx.AsyncClient(timeout=20.0) as http:
        response = await http.post(
            url,
            headers={
                **_auth_headers(access_token),
                "Content-Type": "application/json",
            },
            json={
                "topicName": topic,
                "labelIds": ["INBOX"],
                "labelFilterBehavior": "INCLUDE",
            },
        )

    if response.status_code == 401:
        raise GmailUnauthorizedError("Gmail access token expired")
    if response.is_error:
        raise GmailOAuthError("Unable to start Gmail mailbox watch")

    try:
        payload = response.json()
        history_id = str(payload.get("historyId") or "").strip()
        expiration_ms = int(payload.get("expiration"))
    except (ValueError, TypeError) as exc:
        raise GmailOAuthError(
            "Google returned an invalid Gmail watch response"
        ) from exc

    if not history_id or expiration_ms <= 0:
        raise GmailOAuthError(
            "Google returned an incomplete Gmail watch response"
        )

    return {
        "history_id": history_id,
        "expiration_ms": expiration_ms,
        "topic": topic,
    }


async def stop_gmail_watch(access_token: str) -> None:
    url = f"{GMAIL_API_BASE}/users/me/stop"

    async with httpx.AsyncClient(timeout=20.0) as http:
        response = await http.post(
            url,
            headers=_auth_headers(access_token),
        )

    if response.status_code == 401:
        raise GmailUnauthorizedError("Gmail access token expired")
    if response.is_error:
        raise GmailOAuthError("Unable to stop Gmail mailbox watch")

# -------------------------------------------------------------------------------------------------
# A3D automatic document import client
# -------------------------------------------------------------------------------------------------

class GmailHistoryExpiredError(GmailOAuthError):
    pass


def decode_gmail_body_data(value: str) -> bytes:
    import base64

    encoded = str(value or "").strip()
    if not encoded:
        return b""
    padding = "=" * (-len(encoded) % 4)
    try:
        return base64.urlsafe_b64decode((encoded + padding).encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise GmailOAuthError(
            "Google returned invalid Gmail attachment data"
        ) from exc


async def list_gmail_history(
    access_token: str,
    *,
    start_history_id: str,
    page_token: str | None = None,
) -> dict:
    start = str(start_history_id or "").strip()
    if not start or not start.isdigit():
        raise GmailOAuthError("Gmail processed historyId is invalid")

    params: dict[str, str | int] = {
        "startHistoryId": start,
        "historyTypes": "messageAdded",
        "labelId": "INBOX",
        "maxResults": 500,
    }
    token = str(page_token or "").strip()
    if token:
        params["pageToken"] = token

    url = f"{GMAIL_API_BASE}/users/me/history"
    async with httpx.AsyncClient(timeout=30.0) as http:
        response = await http.get(
            url,
            headers=_auth_headers(access_token),
            params=params,
        )

    if response.status_code == 401:
        raise GmailUnauthorizedError("Gmail access token expired")
    if response.status_code == 404:
        raise GmailHistoryExpiredError(
            "Gmail history cursor is no longer available"
        )
    if response.is_error:
        raise GmailOAuthError("Unable to read Gmail mailbox history")

    try:
        payload = response.json()
    except ValueError as exc:
        raise GmailOAuthError(
            "Google returned an invalid Gmail history response"
        ) from exc

    history_id = str(payload.get("historyId") or "").strip()
    if not history_id or not history_id.isdigit():
        raise GmailOAuthError(
            "Google returned an invalid current Gmail historyId"
        )

    message_ids: list[str] = []
    seen: set[str] = set()
    for history in payload.get("history") or []:
        if not isinstance(history, dict):
            continue
        for added in history.get("messagesAdded") or []:
            if not isinstance(added, dict):
                continue
            message = added.get("message") or {}
            if not isinstance(message, dict):
                continue
            message_id = str(message.get("id") or "").strip()
            if message_id and message_id not in seen:
                seen.add(message_id)
                message_ids.append(message_id)

    return {
        "message_ids": message_ids,
        "history_id": history_id,
        "next_page_token": str(payload.get("nextPageToken") or "").strip(),
    }


async def get_gmail_message(access_token: str, message_id: str) -> dict:
    normalized = str(message_id or "").strip()
    if not normalized:
        raise GmailOAuthError("Gmail message id is required")

    url = f"{GMAIL_API_BASE}/users/me/messages/{normalized}"
    async with httpx.AsyncClient(timeout=30.0) as http:
        response = await http.get(
            url,
            headers=_auth_headers(access_token),
            params={"format": "full"},
        )

    if response.status_code == 401:
        raise GmailUnauthorizedError("Gmail access token expired")
    if response.is_error:
        raise GmailOAuthError("Unable to read Gmail message")

    try:
        payload = response.json()
    except ValueError as exc:
        raise GmailOAuthError(
            "Google returned an invalid Gmail message response"
        ) from exc

    if not isinstance(payload, dict):
        raise GmailOAuthError(
            "Google returned an invalid Gmail message payload"
        )
    return payload


async def get_gmail_attachment(
    access_token: str,
    *,
    message_id: str,
    attachment_id: str,
) -> bytes:
    message = str(message_id or "").strip()
    attachment = str(attachment_id or "").strip()
    if not message or not attachment:
        raise GmailOAuthError(
            "Gmail message and attachment ids are required"
        )

    url = (
        f"{GMAIL_API_BASE}/users/me/messages/{message}"
        f"/attachments/{attachment}"
    )
    async with httpx.AsyncClient(timeout=30.0) as http:
        response = await http.get(
            url,
            headers=_auth_headers(access_token),
        )

    if response.status_code == 401:
        raise GmailUnauthorizedError("Gmail access token expired")
    if response.is_error:
        raise GmailOAuthError("Unable to read Gmail attachment")

    try:
        payload = response.json()
    except ValueError as exc:
        raise GmailOAuthError(
            "Google returned an invalid Gmail attachment response"
        ) from exc

    if not isinstance(payload, dict):
        raise GmailOAuthError(
            "Google returned an invalid Gmail attachment payload"
        )

    data = decode_gmail_body_data(str(payload.get("data") or ""))
    if not data:
        raise GmailOAuthError("Gmail attachment is empty")
    return data
