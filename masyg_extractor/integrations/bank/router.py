from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from masyg_extractor.config.jwt_config import get_current_user_from_cookie
from masyg_extractor.integrations.bank.plaid_client import PlaidApiError, PlaidConfigurationError
from masyg_extractor.integrations.bank.repository import BankRepositoryConfigurationError
from masyg_extractor.integrations.bank.reconciliation import BankReconciliationService
from masyg_extractor.integrations.bank.service import BankService
from masyg_extractor.integrations.bank.webhook_repository import (
    BankWebhookRepository,
)
from masyg_extractor.integrations.bank.webhook_verifier import (
    PlaidWebhookVerificationError,
    PlaidWebhookVerifier,
)
from masyg_extractor.services.my_log import logger


router = APIRouter(prefix="/integrations/bank", tags=["Bank"])
_webhook_verifier = PlaidWebhookVerifier()


class ExchangePublicTokenRequest(BaseModel):
    public_token: str = Field(min_length=1)
    institution_id: str | None = None
    institution_name: str | None = None


class BankTransactionIdentityRequest(BaseModel):
    item_id: str = Field(min_length=1)


class MatchBankTransactionRequest(BaseModel):
    item_id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    file_id: str = Field(min_length=1)


class UpdateBankTransactionStatusRequest(BaseModel):
    item_id: str = Field(min_length=1)
    status: str = Field(min_length=1)


def _user_id(current_user: dict[str, Any]) -> str:
    user_id = str(current_user.get("userId") or "").strip()
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not authenticated")
    return user_id


def _service_for(current_user: dict[str, Any]) -> BankService:
    return BankService(_user_id(current_user))


def _reconciliation_for(
    current_user: dict[str, Any],
) -> BankReconciliationService:
    return BankReconciliationService(_user_id(current_user))


def _raise_bank_error(exc: Exception) -> None:
    if isinstance(exc, KeyError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc.args[0] if exc.args else "Bank connection not found."),
        ) from exc

    if isinstance(exc, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if isinstance(exc, (PlaidConfigurationError, BankRepositoryConfigurationError)):
        logger.warning("Bank integration unavailable error_type=%s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Bank connection is not configured.",
        ) from exc

    if isinstance(exc, PlaidApiError):
        logger.warning(
            "Bank provider request failed code=%s request_id=%s",
            exc.error_code or "unknown",
            exc.request_id or "unknown",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"message": "Bank connection provider request failed.", "code": exc.error_code},
        ) from exc

    logger.exception("Unexpected bank integration failure")
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Bank integration request failed.",
    ) from exc


@router.post("/webhook")
async def plaid_webhook(request: Request):
    # Plaid is the authenticated caller here, not a browser user.
    # Verification must therefore happen against Plaid's signed JWT and the
    # exact raw request body before JSON parsing.
    raw_body = await request.body()
    signed_jwt = request.headers.get("Plaid-Verification")

    try:
        claims = await _webhook_verifier.verify(raw_body, signed_jwt)
    except PlaidWebhookVerificationError as exc:
        logger.warning(
            "Plaid webhook rejected verification_error=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Plaid webhook signature.",
        ) from exc
    except (PlaidConfigurationError, PlaidApiError) as exc:
        logger.warning(
            "Plaid webhook verification unavailable error_type=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Plaid webhook verification is unavailable.",
        ) from exc

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Plaid webhook payload.",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Plaid webhook payload.",
        )

    webhook_type = str(payload.get("webhook_type") or "").strip()
    webhook_code = str(payload.get("webhook_code") or "").strip()
    item_id = str(payload.get("item_id") or "").strip()

    if not webhook_type or not webhook_code or not item_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Plaid webhook payload.",
        )

    repository = BankWebhookRepository()
    delivery = await asyncio.to_thread(
        repository.record_verified_event,
        payload=payload,
        claims=claims,
        raw_body=raw_body,
        signed_jwt=signed_jwt,
    )

    # B2 is durable ingestion/routing only. Processing is intentionally
    # asynchronous in the next wave so Plaid receives a fast acknowledgement.
    return {
        "received": True,
        "duplicate": not delivery["created"],
        "routed": bool(delivery["user_id"]),
    }


@router.post("/link-token")
async def create_link_token(current_user: dict = Depends(get_current_user_from_cookie)):
    try:
        return await _service_for(current_user).create_link_token()
    except Exception as exc:
        _raise_bank_error(exc)


@router.post("/exchange")
async def exchange_public_token(
    payload: ExchangePublicTokenRequest,
    current_user: dict = Depends(get_current_user_from_cookie),
):
    try:
        return await _service_for(current_user).exchange_public_token(
            payload.public_token,
            institution_id=payload.institution_id,
            institution_name=payload.institution_name,
        )
    except Exception as exc:
        _raise_bank_error(exc)


@router.get("/accounts")
async def get_accounts(current_user: dict = Depends(get_current_user_from_cookie)):
    try:
        return await _service_for(current_user).accounts()
    except Exception as exc:
        _raise_bank_error(exc)


@router.get("/transactions")
async def get_transactions(
    limit: int = Query(100, ge=1, le=500),
    current_user: dict = Depends(get_current_user_from_cookie),
):
    try:
        return await _service_for(current_user).transactions(limit=limit)
    except Exception as exc:
        _raise_bank_error(exc)


@router.get("/reconciliation")
async def get_reconciliation(
    limit: int = Query(500, ge=1, le=500),
    current_user: dict = Depends(get_current_user_from_cookie),
):
    try:
        return await _reconciliation_for(current_user).reconciliation(
            limit=limit,
        )
    except Exception as exc:
        _raise_bank_error(exc)


@router.post("/transactions/{transaction_id}/match")
async def match_bank_transaction(
    transaction_id: str,
    payload: MatchBankTransactionRequest,
    current_user: dict = Depends(get_current_user_from_cookie),
):
    try:
        return await _reconciliation_for(current_user).match_transaction(
            item_id=payload.item_id,
            transaction_id=transaction_id,
            group_id=payload.group_id,
            file_id=payload.file_id,
        )
    except Exception as exc:
        _raise_bank_error(exc)


@router.post("/transactions/{transaction_id}/unmatch")
async def unmatch_bank_transaction(
    transaction_id: str,
    payload: BankTransactionIdentityRequest,
    current_user: dict = Depends(get_current_user_from_cookie),
):
    try:
        return await _reconciliation_for(current_user).unmatch_transaction(
            item_id=payload.item_id,
            transaction_id=transaction_id,
        )
    except Exception as exc:
        _raise_bank_error(exc)


@router.post("/transactions/{transaction_id}/status")
async def update_bank_transaction_status(
    transaction_id: str,
    payload: UpdateBankTransactionStatusRequest,
    current_user: dict = Depends(get_current_user_from_cookie),
):
    try:
        return await _reconciliation_for(current_user).set_status(
            item_id=payload.item_id,
            transaction_id=transaction_id,
            reconciliation_status=payload.status,
        )
    except Exception as exc:
        _raise_bank_error(exc)


@router.post("/items/{item_id}/link-token")
async def create_update_link_token(
    item_id: str,
    current_user: dict = Depends(get_current_user_from_cookie),
):
    try:
        return await _service_for(current_user).create_update_link_token(item_id)
    except Exception as exc:
        _raise_bank_error(exc)


@router.delete("/items/{item_id}")
async def disconnect_item(
    item_id: str,
    current_user: dict = Depends(get_current_user_from_cookie),
):
    try:
        return await _service_for(current_user).disconnect(item_id)
    except Exception as exc:
        _raise_bank_error(exc)


@router.post("/transactions/sync")
async def sync_transactions(current_user: dict = Depends(get_current_user_from_cookie)):
    try:
        return await _service_for(current_user).sync_transactions()
    except Exception as exc:
        _raise_bank_error(exc)
