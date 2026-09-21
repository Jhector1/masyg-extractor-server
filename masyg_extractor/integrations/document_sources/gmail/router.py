from __future__ import annotations

import asyncio
import os
from urllib.parse import urlencode

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.responses import RedirectResponse, Response

from masyg_extractor.config.jwt_config import (
    get_current_user_from_cookie,
)
from masyg_extractor.integrations.document_sources.gmail.client import (
    GmailConfigurationError,
    GmailOAuthError,
    build_authorization_url,
    exchange_authorization_code,
    get_gmail_profile,
    revoke_google_token,
)
from masyg_extractor.integrations.document_sources.gmail.oauth_state import (
    GmailStateError,
    issue_gmail_state,
    read_gmail_state,
)
from masyg_extractor.integrations.document_sources.gmail.pubsub import (
    GmailPubSubAuthenticationError,
    GmailPubSubConfigurationError,
    GmailPubSubPayloadError,
    decode_gmail_notification,
    verify_push_authorization,
)
from masyg_extractor.integrations.document_sources.gmail.repository import (
    GmailCredentialRepository,
    GmailMailboxOwnershipError,
)
from masyg_extractor.integrations.document_sources.gmail.service import (
    ensure_gmail_watch,
    stop_gmail_watch_for_user,
)
from masyg_extractor.services.my_log import logger


router = APIRouter(
    prefix="/integrations/gmail",
    tags=["Gmail"],
)


def _user_id(current_user: dict) -> str:
    user_id = str(
        current_user.get("userId") or ""
    ).strip()
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated user is required",
        )
    return user_id


def _integration_return_url(result: str) -> str:
    client_url = (os.getenv("CLIENT_URL") or "").strip()
    if not client_url:
        raise GmailConfigurationError(
            "CLIENT_URL is required"
        )

    query = urlencode({"gmail": result})
    return (
        f"{client_url.rstrip('/')}"
        f"/data/integration/gmail?{query}"
    )


@router.get("/status")
async def gmail_status(
    current_user: dict = Depends(
        get_current_user_from_cookie
    ),
):
    user_id = _user_id(current_user)
    repository = GmailCredentialRepository(user_id)

    try:
        connected = await asyncio.to_thread(
            repository.is_connected
        )
        email_address = (
            await asyncio.to_thread(
                repository.email_address
            )
            if connected
            else None
        )
        watch = (
            await asyncio.to_thread(
                repository.watch_state
            )
            if connected
            else {}
        )
        if connected:
            await asyncio.to_thread(
                repository.ensure_mailbox_owner
            )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gmail connection status is unavailable",
        ) from exc

    return {
        "provider": "gmail",
        "connected": connected,
        "email_address": email_address,
        "watch_status": (
            watch.get("status")
            if connected
            else None
        ),
        "watch_expiration_ms": (
            watch.get("expirationMs")
            if connected
            else None
        ),
    }


@router.post("/connect")
async def gmail_connect(
    current_user: dict = Depends(
        get_current_user_from_cookie
    ),
):
    user_id = _user_id(current_user)

    try:
        state = issue_gmail_state(user_id)
        authorization_url = build_authorization_url(
            state
        )
    except (
        GmailConfigurationError,
        GmailStateError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gmail connection is not configured",
        ) from exc

    return {
        "authorization_url": authorization_url
    }


@router.get("/callback")
async def gmail_callback(
    code: str | None = Query(default=None),
    state_value: str | None = Query(
        default=None,
        alias="state",
    ),
    error: str | None = Query(default=None),
):
    if error or not code or not state_value:
        logger.warning(
            "Gmail OAuth callback rejected before token exchange: "
            "google_error=%r code_present=%s state_present=%s",
            error,
            bool(code),
            bool(state_value),
        )
        return RedirectResponse(
            _integration_return_url("error"),
            status_code=303,
        )

    try:
        user_id = read_gmail_state(state_value)
        token = await exchange_authorization_code(
            code
        )
        profile = await get_gmail_profile(
            token["access_token"]
        )

        repository = GmailCredentialRepository(
            user_id
        )
        previous_email = await asyncio.to_thread(
            repository.email_address
        )

        mailbox_claim_created = await asyncio.to_thread(
            repository.claim_mailbox_owner,
            email_address,
        )

        try:
            await asyncio.to_thread(
                repository.store_token,
                access_token=token["access_token"],
                refresh_token=token["refresh_token"],
                expires_in=token["expires_in"],
                scope=token["scope"],
                email_address=profile["email_address"],
                history_id=profile["history_id"],
            )
        except Exception:
            if mailbox_claim_created:
                await asyncio.to_thread(
                    repository.release_mailbox_owner,
                    email_address,
                )
            raise

        if (
            previous_email
            and previous_email
            != profile["email_address"]
        ):
            await asyncio.to_thread(
                repository.release_mailbox_owner,
                previous_email,
            )

    except (
        GmailConfigurationError,
        GmailOAuthError,
        GmailStateError,
        GmailMailboxOwnershipError,
    ) as exc:
        logger.warning(
            "Gmail OAuth callback failed: %s: %s",
            type(exc).__name__,
            exc,
        )
        return RedirectResponse(
            _integration_return_url("error"),
            status_code=303,
        )

    try:
        watch_result = await ensure_gmail_watch(
            user_id
        )
        logger.info(
            "Gmail watch bootstrap user_id=%s active=%s",
            user_id,
            watch_result.get("active"),
        )
    except GmailConfigurationError:
        logger.info(
            "Gmail connected without mailbox watch: "
            "Pub/Sub topic is not configured"
        )
    except Exception as exc:
        logger.warning(
            "Gmail connected but mailbox watch bootstrap failed "
            "user_id=%s error_type=%s",
            user_id,
            type(exc).__name__,
        )

    return RedirectResponse(
        _integration_return_url("connected"),
        status_code=303,
    )


@router.post("/pubsub")
async def gmail_pubsub_push(
    request: Request,
):
    try:
        await asyncio.to_thread(
            verify_push_authorization,
            request.headers.get("Authorization"),
        )
    except GmailPubSubConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Gmail Pub/Sub push authentication "
                "is not configured"
            ),
        ) from exc
    except GmailPubSubAuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Gmail Pub/Sub identity",
        ) from exc

    try:
        envelope = await request.json()
        notification = decode_gmail_notification(
            envelope
        )
    except (
        ValueError,
        GmailPubSubPayloadError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Gmail Pub/Sub payload",
        ) from exc

    user_id = await asyncio.to_thread(
        GmailCredentialRepository.owner_user_id_for_email,
        notification["email_address"],
    )

    if not user_id:
        logger.info(
            "Ignoring Gmail notification for "
            "unregistered mailbox"
        )
        return Response(status_code=204)

    repository = GmailCredentialRepository(
        user_id
    )
    connected_email = await asyncio.to_thread(
        repository.email_address
    )
    if (
        connected_email
        != notification["email_address"]
    ):
        logger.warning(
            "Ignoring Gmail notification with "
            "stale mailbox registry user_id=%s",
            user_id,
        )
        return Response(status_code=204)

    try:
        await asyncio.to_thread(
            repository.record_notification,
            history_id=notification["history_id"],
            message_id=notification["message_id"],
            publish_time=notification["publish_time"],
        )
    except Exception as exc:
        logger.warning(
            "Unable to durably record Gmail notification "
            "user_id=%s error_type=%s",
            user_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gmail notification could not be recorded",
        ) from exc

    return Response(status_code=204)


@router.post("/disconnect")
async def gmail_disconnect(
    current_user: dict = Depends(
        get_current_user_from_cookie
    ),
):
    user_id = _user_id(current_user)
    repository = GmailCredentialRepository(
        user_id
    )
    refresh_token = await asyncio.to_thread(
        repository.refresh_token
    )

    if refresh_token:
        try:
            await stop_gmail_watch_for_user(
                user_id
            )
        except Exception as exc:
            logger.warning(
                "Gmail watch stop failed during disconnect "
                "user_id=%s error_type=%s",
                user_id,
                type(exc).__name__,
            )

        try:
            await revoke_google_token(
                refresh_token
            )
        except GmailOAuthError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "Gmail authorization could not be "
                    "revoked. Please try again."
                ),
            ) from exc

    await asyncio.to_thread(
        repository.disconnect
    )

    return {
        "provider": "gmail",
        "connected": False,
    }
