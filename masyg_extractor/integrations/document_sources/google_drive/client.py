from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import quote, urlencode

import httpx


GOOGLE_AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
GOOGLE_DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"

SUPPORTED_IMPORT_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/tiff",
        "image/bmp",
    }
)


class GoogleDriveConfigurationError(RuntimeError):
    pass


class GoogleDriveOAuthError(RuntimeError):
    pass


class GoogleDriveUnauthorizedError(GoogleDriveOAuthError):
    pass


class GoogleDriveFileError(RuntimeError):
    pass


@dataclass(frozen=True)
class GoogleDriveOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str


def oauth_config() -> GoogleDriveOAuthConfig:
    client_id = (os.getenv("GOOGLE_DRIVE_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("GOOGLE_DRIVE_CLIENT_SECRET") or "").strip()
    redirect_uri = (os.getenv("GOOGLE_DRIVE_REDIRECT_URI") or "").strip()

    missing = [
        name
        for name, value in (
            ("GOOGLE_DRIVE_CLIENT_ID", client_id),
            ("GOOGLE_DRIVE_CLIENT_SECRET", client_secret),
            ("GOOGLE_DRIVE_REDIRECT_URI", redirect_uri),
        )
        if not value
    ]
    if missing:
        raise GoogleDriveConfigurationError(
            "Missing Google Drive OAuth configuration: " + ", ".join(missing)
        )

    return GoogleDriveOAuthConfig(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
    )


def build_authorization_url(state: str) -> str:
    if not state:
        raise ValueError("state is required")

    config = oauth_config()
    query = urlencode(
        {
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "response_type": "code",
            "scope": GOOGLE_DRIVE_FILE_SCOPE,
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
            "state": state,
        }
    )
    return f"{GOOGLE_AUTHORIZATION_URL}?{query}"


async def exchange_authorization_code(code: str) -> dict:
    normalized = str(code or "").strip()
    if not normalized:
        raise GoogleDriveOAuthError("Authorization code is required")

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
        raise GoogleDriveOAuthError("Google Drive authorization failed")

    try:
        payload = response.json()
    except ValueError as exc:
        raise GoogleDriveOAuthError(
            "Google Drive returned an invalid token response"
        ) from exc

    access_token = str(payload.get("access_token") or "").strip()
    refresh_token = str(payload.get("refresh_token") or "").strip()
    expires_in = payload.get("expires_in")

    try:
        expires_in = int(expires_in)
    except (TypeError, ValueError) as exc:
        raise GoogleDriveOAuthError("Google Drive token expiry is invalid") from exc

    if not access_token or not refresh_token or expires_in <= 0:
        raise GoogleDriveOAuthError(
            "Google Drive did not return durable authorization"
        )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": expires_in,
        "scope": str(payload.get("scope") or GOOGLE_DRIVE_FILE_SCOPE),
        "token_type": str(payload.get("token_type") or "Bearer"),
    }


async def refresh_access_token(refresh_token: str) -> dict:
    normalized = str(refresh_token or "").strip()
    if not normalized:
        raise GoogleDriveOAuthError("Google Drive refresh token is missing")

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
        raise GoogleDriveOAuthError("Google Drive token refresh failed")

    try:
        payload = response.json()
        access_token = str(payload.get("access_token") or "").strip()
        expires_in = int(payload.get("expires_in"))
    except (ValueError, TypeError) as exc:
        raise GoogleDriveOAuthError(
            "Google Drive returned an invalid refresh response"
        ) from exc

    if not access_token or expires_in <= 0:
        raise GoogleDriveOAuthError("Google Drive token refresh was incomplete")

    return {
        "access_token": access_token,
        "expires_in": expires_in,
        "token_type": str(payload.get("token_type") or "Bearer"),
    }


def _auth_headers(access_token: str) -> dict[str, str]:
    normalized = str(access_token or "").strip()
    if not normalized:
        raise GoogleDriveUnauthorizedError("Google Drive access token is missing")
    return {"Authorization": f"Bearer {normalized}", "Accept": "application/json"}


async def get_drive_file_metadata(file_id: str, access_token: str) -> dict:
    normalized_id = str(file_id or "").strip()
    if not normalized_id:
        raise GoogleDriveFileError("Google Drive file id is required")

    url = f"{GOOGLE_DRIVE_API_BASE}/files/{quote(normalized_id, safe='')}"
    async with httpx.AsyncClient(timeout=20.0) as http:
        response = await http.get(
            url,
            params={
                "fields": "id,name,mimeType,size,capabilities(canDownload)",
                "supportsAllDrives": "true",
            },
            headers=_auth_headers(access_token),
        )

    if response.status_code == 401:
        raise GoogleDriveUnauthorizedError("Google Drive access token expired")
    if response.is_error:
        raise GoogleDriveFileError("Unable to read the selected Google Drive file")

    try:
        metadata = response.json()
    except ValueError as exc:
        raise GoogleDriveFileError("Google Drive returned invalid file metadata") from exc

    mime_type = str(metadata.get("mimeType") or "").strip()
    if mime_type not in SUPPORTED_IMPORT_MIME_TYPES:
        raise GoogleDriveFileError(
            "Only PDF, JPEG, PNG, TIFF, and WebP files can be imported"
        )

    capabilities = metadata.get("capabilities") or {}
    if capabilities.get("canDownload") is not True:
        raise GoogleDriveFileError("The selected Google Drive file cannot be downloaded")

    name = str(metadata.get("name") or "").strip()
    if not name:
        raise GoogleDriveFileError("The selected Google Drive file has no filename")

    return metadata


async def download_drive_file_content(file_id: str, access_token: str) -> bytes:
    normalized_id = str(file_id or "").strip()
    if not normalized_id:
        raise GoogleDriveFileError("Google Drive file id is required")

    url = f"{GOOGLE_DRIVE_API_BASE}/files/{quote(normalized_id, safe='')}"
    async with httpx.AsyncClient(timeout=60.0) as http:
        response = await http.get(
            url,
            params={"alt": "media", "supportsAllDrives": "true"},
            headers=_auth_headers(access_token),
        )

    if response.status_code == 401:
        raise GoogleDriveUnauthorizedError("Google Drive access token expired")
    if response.is_error:
        raise GoogleDriveFileError("Unable to download the selected Google Drive file")
    if not response.content:
        raise GoogleDriveFileError("The selected Google Drive file is empty")

    return response.content


async def download_drive_file(file_id: str, access_token: str) -> tuple[dict, bytes]:
    metadata = await get_drive_file_metadata(file_id, access_token)
    content = await download_drive_file_content(file_id, access_token)
    return metadata, content
