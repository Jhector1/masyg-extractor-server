from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

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
from masyg_extractor.integrations.accounting.shared.execution_plan import (
    build_accounting_execution_plan,
)
from masyg_extractor.integrations.accounting.shared.document_materializer import (
    materialize_accounting_document,
)
from masyg_extractor.integrations.accounting.shared.execution_bridge import (
    build_accounting_execution_bridge,
)
from masyg_extractor.integrations.accounting.shared.execution_result import (
    summarize_accounting_provider_result,
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


@router.post("/execution-plan")
async def post_accounting_execution_plan(
    payload: dict[str, Any],
    current_user: dict = Depends(
        get_current_user_from_cookie
    ),
):
    # Re-run canonical preflight at plan time rather than trusting
    # client-supplied readiness state.
    preflight = await post_accounting_batch_preflight(
        payload,
        current_user,
    )

    return build_accounting_execution_plan(
        provider=preflight["provider"],
        documents=preflight["documents"],
    )



@router.post("/execute")
async def post_accounting_execution(
    request: Request,
    payload: dict[str, Any],
    current_user: dict = Depends(
        get_current_user_from_cookie
    ),
):
    """
    Execute only the documents that remain canonically ready after
    a fresh server-side preflight and execution plan.

    The browser supplies identities and explicit confirmation only.
    It cannot supply readiness, provider actions, accounting intent,
    provider payloads, or a trusted execution plan.
    """

    user_id = str(
        current_user.get("userId") or ""
    ).strip()

    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="User not authenticated.",
        )

    client_id = str(
        request.session.get("client_id")
        or ""
    ).strip()

    if not client_id:
        raise HTTPException(
            status_code=400,
            detail="Client ID not found in session.",
        )

    # Require the JSON boolean true. Values such as 1, "true",
    # or any truthy object are not an execution confirmation.
    if payload.get("confirmed") is not True:
        raise HTTPException(
            status_code=400,
            detail=(
                "Explicit accounting execution confirmation "
                "is required."
            ),
        )

    # Never pass browser-supplied readiness/plan/action fields into
    # the canonical preflight owner.
    preflight_payload = {
        "provider": payload.get("provider"),
        "documents": payload.get("documents"),
    }

    fresh_preflight = (
        await post_accounting_batch_preflight(
            preflight_payload,
            current_user,
        )
    )

    plan = build_accounting_execution_plan(
        provider=fresh_preflight["provider"],
        documents=fresh_preflight["documents"],
    )

    provider = plan["provider"]

    client = await get_firestore_client()

    executions: list[dict[str, Any]] = []
    runtime_blocked: list[dict[str, Any]] = []

    def block_runtime_document(
        plan_document: dict[str, Any],
        reason: str,
    ) -> None:
        runtime_blocked.append(
            {
                "provider": provider,
                "group_id": plan_document.get(
                    "group_id"
                ),
                "file_id": plan_document.get(
                    "file_id"
                ),
                "document_type": (
                    plan_document.get(
                        "document_type"
                    )
                ),
                "accounting_intent": (
                    plan_document.get(
                        "accounting_intent"
                    )
                ),
                "state": "unavailable",
                "reason": reason,
                "durable_status": (
                    plan_document.get(
                        "durable_status"
                    )
                ),
            }
        )

    for execution_group in plan["groups"]:
        accounting_intent = str(
            execution_group.get(
                "accounting_intent"
            )
            or ""
        ).strip()

        group_documents = (
            execution_group.get(
                "documents"
            )
            or []
        )

        # Phase 1: reload every exact source document and re-resolve
        # its accounting meaning before provider runtime construction.
        source_candidates: list[
            tuple[
                dict[str, Any],
                dict[str, Any],
            ]
        ] = []

        for plan_document in group_documents:
            group_id = str(
                plan_document.get(
                    "group_id"
                )
                or ""
            ).strip()

            file_id = str(
                plan_document.get(
                    "file_id"
                )
                or ""
            ).strip()

            if not group_id or not file_id:
                block_runtime_document(
                    plan_document,
                    (
                        "Canonical accounting document "
                        "identity is missing."
                    ),
                )
                continue

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
                block_runtime_document(
                    plan_document,
                    "Document no longer exists.",
                )
                continue

            source_document = (
                snapshot.to_dict()
                or {}
            )

            try:
                fresh_handoff = (
                    resolve_accounting_document_handoff(
                        source_document,
                        group_id=group_id,
                        file_id=file_id,
                    )
                )
            except ValueError as exc:
                block_runtime_document(
                    plan_document,
                    str(exc),
                )
                continue

            if (
                fresh_handoff.get(
                    "group_id"
                )
                != group_id
                or fresh_handoff.get(
                    "file_id"
                )
                != file_id
            ):
                block_runtime_document(
                    plan_document,
                    (
                        "Canonical accounting document "
                        "identity changed after preflight."
                    ),
                )
                continue

            if (
                fresh_handoff.get(
                    "accounting_intent"
                )
                != accounting_intent
            ):
                block_runtime_document(
                    plan_document,
                    (
                        "Document accounting intent "
                        "changed after preflight."
                    ),
                )
                continue

            source_candidates.append(
                (
                    plan_document,
                    source_document,
                )
            )

        if not source_candidates:
            continue

        # Phase 2: construct the provider owner only after the fresh
        # source/handoff gate has passed.
        try:
            bridge = (
                build_accounting_execution_bridge(
                    request=request,
                    user_id=user_id,
                    provider=provider,
                    accounting_intent=(
                        accounting_intent
                    ),
                )
            )
        except Exception as exc:
            reason = (
                str(exc).strip()
                or (
                    "Unable to initialize accounting "
                    "provider execution."
                )
            )

            for (
                plan_document,
                _source_document,
            ) in source_candidates:
                block_runtime_document(
                    plan_document,
                    reason,
                )

            continue

        # Phase 3: materialize exact canonical identities through the
        # existing provider field-mapping factory.
        materialized_documents = []
        dispatched_identities = []

        for (
            plan_document,
            source_document,
        ) in source_candidates:
            group_id = str(
                plan_document["group_id"]
            )

            file_id = str(
                plan_document["file_id"]
            )

            try:
                materialized = (
                    materialize_accounting_document(
                        source_document,
                        group_id=group_id,
                        file_id=file_id,
                        factory=(
                            bridge.document_factory
                        ),
                    )
                )
            except Exception as exc:
                block_runtime_document(
                    plan_document,
                    (
                        str(exc).strip()
                        or (
                            "Accounting document could "
                            "not be materialized."
                        )
                    ),
                )
                continue

            materialized_documents.append(
                materialized
            )

            dispatched_identities.append(
                {
                    "group_id": group_id,
                    "file_id": file_id,
                }
            )

        if not materialized_documents:
            continue

        # Phase 4: delegate to the existing protected provider bulk
        # service. Atomic claim_record remains the ultimate TOCTOU
        # duplicate barrier.
        try:
            provider_result = (
                await bridge.send_documents(
                    materialized_documents
                )
            )
        except Exception as exc:
            executions.append(
                {
                    "provider": provider,
                    "accounting_intent": (
                        accounting_intent
                    ),
                    "status": "failed",
                    "documents": (
                        dispatched_identities
                    ),
                    "error": (
                        str(exc).strip()
                        or (
                            "Accounting provider "
                            "execution failed."
                        )
                    ),
                }
            )
            continue

        execution_entry = {
            "provider": provider,
            "accounting_intent": (
                accounting_intent
            ),
            # `completed` means the provider-service call returned.
            # Per-document provider success/failure is represented
            # separately by the verified `outcome` summary below.
            "status": "completed",
            "documents": (
                dispatched_identities
            ),
            "result": provider_result,
        }

        provider_outcome = (
            summarize_accounting_provider_result(
                provider_result,
                dispatched_identities,
            )
        )

        # Preserve compatibility with older/non-canonical provider
        # result shapes while never inventing successful counts.
        # Current QuickBooks/Xero bulk owners return the canonical
        # AccountingOperationProgress result envelope.
        if provider_outcome is not None:
            execution_entry["outcome"] = (
                provider_outcome
            )

        executions.append(execution_entry)

    return {
        "provider": provider,
        "confirmed": True,
        # Preserve the complete fresh plan, including all original
        # sending/succeeded/uncertain/unsupported/unavailable blocks.
        "plan": plan,
        # Runtime blocks are additional changes detected after the
        # fresh preflight while reloading/materializing the sources.
        "runtime_blocked": runtime_blocked,
        "executions": executions,
    }


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
