from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from masyg_extractor.config.jwt_config import get_current_user_from_cookie
from masyg_extractor.integrations.bank.plaid_client import PlaidApiError, PlaidConfigurationError
from masyg_extractor.integrations.bank.repository import BankRepositoryConfigurationError
from masyg_extractor.integrations.bank.service import BankService
from masyg_extractor.services.my_log import logger


router = APIRouter(prefix="/integrations/bank", tags=["Bank"])


class ExchangePublicTokenRequest(BaseModel):
    public_token: str = Field(min_length=1)
    institution_id: str | None = None
    institution_name: str | None = None


def _user_id(current_user: dict[str, Any]) -> str:
    user_id = str(current_user.get("userId") or "").strip()
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not authenticated")
    return user_id


def _service_for(current_user: dict[str, Any]) -> BankService:
    return BankService(_user_id(current_user))


def _raise_bank_error(exc: Exception) -> None:
    if isinstance(exc, KeyError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc.args[0] if exc.args else "Bank connection not found."),
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
