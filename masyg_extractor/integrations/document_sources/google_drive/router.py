from __future__ import annotations

from masyg_extractor.services.subscription_access import require_active_subscription
import io
import os
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from masyg_extractor.config.jwt_config import get_current_user_from_cookie
from masyg_extractor.integrations.document_sources.google_drive.client import (
    GoogleDriveConfigurationError,
    GoogleDriveFileError,
    GoogleDriveOAuthError,
    GoogleDriveUnauthorizedError,
    build_authorization_url,
    download_drive_file_content,
    exchange_authorization_code,
    get_drive_file_metadata,
    refresh_access_token,
)
from masyg_extractor.integrations.document_sources.google_drive.oauth_state import (
    GoogleDriveStateError,
    issue_google_drive_state,
    read_google_drive_state,
)
from masyg_extractor.integrations.document_sources.google_drive.repository import (
    GoogleDriveCredentialRepository,
)
from masyg_extractor.services.document_ingestion import (
    DocumentImportLimitError,
    ingest_documents,
    max_import_files,
    validate_import_count,
)
from masyg_extractor.services.progress_log import (
    ExtractorProgressLog,
    get_extractor_progress_logger,
)


router = APIRouter(prefix="/integrations/google-drive", tags=["Google Drive"])


class GoogleDriveImportRequest(BaseModel):
    file_ids: list[str]


def _user_id(current_user: dict) -> str:
    user_id = str(current_user.get("userId") or "").strip()
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated user is required",
        )
    return user_id


def _integration_return_url(result: str) -> str:
    client_url = (os.getenv("CLIENT_URL") or "").strip()
    if not client_url:
        raise GoogleDriveConfigurationError("CLIENT_URL is required")
    query = urlencode({"googleDrive": result})
    return f"{client_url.rstrip('/')}/data/integration/google-drive?{query}"


@router.get("/status")
async def google_drive_status(
    current_user: dict = Depends(get_current_user_from_cookie),
):
    user_id = _user_id(current_user)
    try:
        connected = GoogleDriveCredentialRepository(user_id).is_connected()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Drive connection status is unavailable",
        ) from exc
    return {
        "provider": "google_drive",
        "connected": connected,
        "max_import_files": max_import_files(),
    }


@router.post("/connect")
async def google_drive_connect(
    current_user: dict = Depends(get_current_user_from_cookie),
):
    user_id = _user_id(current_user)
    try:
        state = issue_google_drive_state(user_id)
        authorization_url = build_authorization_url(state)
    except (GoogleDriveConfigurationError, GoogleDriveStateError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Drive connection is not configured",
        ) from exc
    return {"authorization_url": authorization_url}


@router.get("/callback")
async def google_drive_callback(
    code: str | None = Query(default=None),
    state_value: str | None = Query(default=None, alias="state"),
    error: str | None = Query(default=None),
):
    if error or not code or not state_value:
        return RedirectResponse(_integration_return_url("error"), status_code=303)

    try:
        user_id = read_google_drive_state(state_value)
        token = await exchange_authorization_code(code)
        GoogleDriveCredentialRepository(user_id).store_token(
            access_token=token["access_token"],
            refresh_token=token["refresh_token"],
            expires_in=token["expires_in"],
            scope=token["scope"],
            token_type=token["token_type"],
        )
    except (
        GoogleDriveConfigurationError,
        GoogleDriveOAuthError,
        GoogleDriveStateError,
    ):
        return RedirectResponse(_integration_return_url("error"), status_code=303)

    return RedirectResponse(_integration_return_url("connected"), status_code=303)


class GoogleDriveImportSession:
    """One request-scoped authorization owner shared by all selected Drive files."""

    def __init__(self, repository: GoogleDriveCredentialRepository):
        self.repository = repository
        self.access_token = repository.access_token()
        self.refresh_token = repository.refresh_token()
        if not self.access_token or not self.refresh_token:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Google Drive is not connected",
            )

    async def _refresh(self) -> None:
        try:
            token = await refresh_access_token(self.refresh_token)
        except GoogleDriveOAuthError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Google Drive authorization expired",
            ) from exc

        self.access_token = token["access_token"]
        self.repository.store_token(
            access_token=self.access_token,
            refresh_token=self.refresh_token,
            expires_in=token["expires_in"],
            token_type=token["token_type"],
        )

    async def metadata(self, file_id: str) -> dict:
        try:
            return await get_drive_file_metadata(file_id, self.access_token)
        except GoogleDriveUnauthorizedError:
            await self._refresh()
            return await get_drive_file_metadata(file_id, self.access_token)

    async def content(self, file_id: str) -> bytes:
        try:
            return await download_drive_file_content(file_id, self.access_token)
        except GoogleDriveUnauthorizedError:
            await self._refresh()
            return await download_drive_file_content(file_id, self.access_token)


class GoogleDriveReadableUpload:
    """
    UploadFile-compatible surface for canonical ingestion.

    Metadata is resolved up front. File bodies are downloaded lazily only when
    canonical ingestion reaches this file's bounded processing chunk.
    """

    def __init__(
        self,
        *,
        session: GoogleDriveImportSession,
        file_id: str,
        metadata: dict,
    ):
        self.session = session
        self.file_id = file_id
        self.filename = str(metadata["name"])
        self.content_type = str(metadata.get("mimeType") or "")
        self.file = io.BytesIO()
        self._loaded = False

    async def read(self) -> bytes:
        if not self._loaded:
            content = await self.session.content(self.file_id)
            self.file.close()
            self.file = io.BytesIO(content)
            self._loaded = True
        return self.file.read()

    async def seek(self, offset: int) -> int:
        return self.file.seek(offset)

    def release(self) -> None:
        self.file.close()
        self.file = io.BytesIO()
        self._loaded = False


async def _prepare_selected_files(
    *,
    repository: GoogleDriveCredentialRepository,
    file_ids: list[str],
) -> list[GoogleDriveReadableUpload]:
    session = GoogleDriveImportSession(repository)
    uploads: list[GoogleDriveReadableUpload] = []

    for file_id in file_ids:
        metadata = await session.metadata(file_id)
        uploads.append(
            GoogleDriveReadableUpload(
                session=session,
                file_id=file_id,
                metadata=metadata,
            )
        )

    return uploads


@router.post("/picker-token")
async def google_drive_picker_token(
    current_user: dict = Depends(require_active_subscription),
):
    user_id = _user_id(current_user)
    repository = GoogleDriveCredentialRepository(user_id)
    refresh_token = repository.refresh_token()

    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Google Drive is not connected",
        )

    try:
        token = await refresh_access_token(refresh_token)
    except GoogleDriveConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Drive connection is not configured",
        ) from exc
    except GoogleDriveOAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google Drive authorization expired",
        ) from exc

    repository.store_token(
        access_token=token["access_token"],
        refresh_token=refresh_token,
        expires_in=token["expires_in"],
        token_type=token["token_type"],
    )

    return {
        "access_token": token["access_token"],
        "expires_in": token["expires_in"],
    }


@router.post("/import", status_code=status.HTTP_201_CREATED)
async def google_drive_import(
    payload: GoogleDriveImportRequest,
    request: Request,
    current_user: dict = Depends(require_active_subscription),
    progress_logger: ExtractorProgressLog = Depends(get_extractor_progress_logger),
):
    """
    Import only file ids explicitly selected by the user.

    Drive owns authorization and byte acquisition; canonical MASYG ingestion owns
    extraction, previews, persistence, progress, and the workspace response shape.
    """
    user_id = _user_id(current_user)
    file_ids = [str(value or "").strip() for value in payload.file_ids]
    file_ids = [value for value in file_ids if value]

    try:
        validate_import_count(len(file_ids))
    except DocumentImportLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if len(set(file_ids)) != len(file_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Duplicate Google Drive file selections are not allowed",
        )

    repository = GoogleDriveCredentialRepository(user_id)
    try:
        uploads = await _prepare_selected_files(
            repository=repository,
            file_ids=file_ids,
        )
    except GoogleDriveFileError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except GoogleDriveOAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Google Drive import is temporarily unavailable",
        ) from exc

    client_id = request.session.get("client_id") or "Guest"
    return await ingest_documents(
        files=uploads,
        user_id=user_id,
        client_id=client_id,
        progress_logger=progress_logger,
    )


@router.post("/disconnect")
async def google_drive_disconnect(
    current_user: dict = Depends(get_current_user_from_cookie),
):
    user_id = _user_id(current_user)
    GoogleDriveCredentialRepository(user_id).disconnect()
    return {"provider": "google_drive", "connected": False}
