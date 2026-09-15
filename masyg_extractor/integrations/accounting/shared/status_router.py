from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from masyg_extractor.config.jwt_config import (
    get_current_user_from_cookie,
)
from masyg_extractor.integrations.accounting.registry import (
    get_accounting_provider,
)
from masyg_extractor.integrations.accounting.shared.batch_preflight import (
    preflight_accounting_document,
    summarize_accounting_preflight,
    unavailable_accounting_preflight_result,
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


@router.post("/preflight")
async def post_accounting_batch_preflight(
    payload: dict[str, Any],
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

    provider = str(
        payload.get("provider") or ""
    ).strip().lower()

    try:
        get_accounting_provider(provider)
    except KeyError as exc:
        raise HTTPException(
            status_code=400,
            detail="Unsupported accounting provider.",
        ) from exc

    documents = payload.get("documents")

    if (
        not isinstance(documents, list)
        or not documents
    ):
        raise HTTPException(
            status_code=400,
            detail="documents must contain at least one document.",
        )

    # This endpoint is read-only. Keep a defensive request ceiling
    # independent from provider execution limits.
    if len(documents) > 100:
        raise HTTPException(
            status_code=400,
            detail="A maximum of 100 documents may be preflighted at once.",
        )

    client = await get_firestore_client()

    repo = QuickBooksFirestoreService(
        user_id=user_id,
        integration=provider,
    )

    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for raw_document in documents:
        if not isinstance(raw_document, dict):
            raise HTTPException(
                status_code=400,
                detail="Each document must be an object.",
            )

        group_id = str(
            raw_document.get("group_id") or ""
        ).strip()

        file_id = str(
            raw_document.get("file_id") or ""
        ).strip()

        if not group_id or not file_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Each document requires group_id and file_id."
                ),
            )

        identity = (
            group_id,
            file_id,
        )

        if identity in seen:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Duplicate accounting document identity."
                ),
            )

        seen.add(identity)

        file_ref = (
            client.collection("users")
            .document(user_id)
            .collection("groups")
            .document(group_id)
            .collection("files")
            .document(file_id)
        )

        snapshot = await document_get(
            file_ref
        )

        if not snapshot.exists:
            results.append(
                unavailable_accounting_preflight_result(
                    provider=provider,
                    group_id=group_id,
                    file_id=file_id,
                    reason="Document not found.",
                )
            )
            continue

        try:
            handoff = (
                resolve_accounting_document_handoff(
                    snapshot.to_dict() or {},
                    group_id=group_id,
                    file_id=file_id,
                )
            )
        except ValueError as exc:
            results.append(
                unavailable_accounting_preflight_result(
                    provider=provider,
                    group_id=group_id,
                    file_id=file_id,
                    reason=str(exc),
                )
            )
            continue

        result = await asyncio.to_thread(
            preflight_accounting_document,
            repo,
            provider=provider,
            handoff=handoff,
        )

        results.append(result)

    return summarize_accounting_preflight(
        provider=provider,
        documents=results,
    )


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
