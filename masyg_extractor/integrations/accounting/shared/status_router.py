from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from masyg_extractor.config.jwt_config import (
    get_current_user_from_cookie,
)
from masyg_extractor.integrations.accounting.shared.document_handoff import (
    resolve_accounting_document_handoff,
)
from masyg_extractor.integrations.accounting.shared.durable_status import (
    read_accounting_durable_status,
)
from masyg_extractor.integrations.accounting.shared.firestore_repository import (
    QuickBooksFirestoreService,
)
from masyg_extractor.services.firestore_helpers import (
    document_get,
    get_firestore_client,
)


router = APIRouter(
    prefix="/integrations/accounting",
)


@router.get("/handoff")
async def get_accounting_document_handoff(
    group_id: str,
    file_id: str,
    current_user: dict = Depends(
        get_current_user_from_cookie
    ),
):
    user_id = str(
        current_user.get("userId") or ""
    ).strip()

    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="User not authenticated.",
        )

    group_id = str(group_id or "").strip()
    file_id = str(file_id or "").strip()

    if not group_id or not file_id:
        raise HTTPException(
            status_code=400,
            detail="group_id and file_id are required.",
        )

    client = await get_firestore_client()

    file_ref = (
        client.collection("users")
        .document(user_id)
        .collection("groups")
        .document(group_id)
        .collection("files")
        .document(file_id)
    )

    snapshot = await document_get(file_ref)

    if not snapshot.exists:
        raise HTTPException(
            status_code=404,
            detail="Document not found.",
        )

    try:
        return resolve_accounting_document_handoff(
            snapshot.to_dict() or {},
            group_id=group_id,
            file_id=file_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc


@router.get("/status")
async def get_accounting_durable_status(
    provider: str,
    intent: str,
    group_id: str,
    file_id: str,
    current_user: dict = Depends(
        get_current_user_from_cookie
    ),
):
    user_id = str(
        current_user.get("userId") or ""
    ).strip()

    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="User not authenticated.",
        )

    try:
        repo = QuickBooksFirestoreService(
            user_id=user_id,
            integration=provider,
        )

        return await asyncio.to_thread(
            read_accounting_durable_status,
            repo,
            provider=provider,
            intent=intent,
            group_id=group_id,
            file_id=file_id,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
