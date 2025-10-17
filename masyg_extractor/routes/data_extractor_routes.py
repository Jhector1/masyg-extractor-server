# masyg_extractor/routes/data_extractor_routes.py
from __future__ import annotations

import asyncio
import base64
import io
import os
from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Request, UploadFile, File, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from firebase_admin import firestore, firestore as admin_fs
from google.api_core.exceptions import FailedPrecondition
from google.cloud.firestore_v1 import FieldFilter

from masyg_extractor.config.jwt_config import get_current_user_from_cookie
from masyg_extractor.services.change_log_services import (
    handle_document_edit,
    handle_document_delete,
    handle_document_add,
    LINE_ITEM_REGEX,
    handle_line_item_update,
    handle_group_delete,
)
from masyg_extractor.services.processing import process_files_in_parallel
from masyg_extractor.services.image_extractor_service import compress_file_blob
from masyg_extractor.services.firestore_helpers import (
    get_firestore_client,
    document_get,
    document_update,
    document_delete,
    stream_collection,
)
from masyg_extractor.services.progress_log import (
    ExtractorProgressLog,
    get_extractor_progress_logger,
)
from masyg_extractor.services.dependencies import get_firebase_user, generate_group_id
from masyg_extractor.services.my_log import send_log, logger
from masyg_extractor.utils.extensions import sio


router = APIRouter(prefix="/extractor")

EVENT_PROGRESS = "data-progress"

from fastapi.encoders import jsonable_encoder

def ok(content: dict | list, status_code: int = 200):
    """Safe JSONResponse that handles Firestore/Datetime types."""
    return JSONResponse(content=jsonable_encoder(content), status_code=status_code)

@router.post("/extract-data", status_code=status.HTTP_201_CREATED)
async def extract_data(
    request: Request,
    files: List[UploadFile] = File(...),
    current_user: dict = Depends(get_current_user_from_cookie),
    progress_logger: ExtractorProgressLog = Depends(get_extractor_progress_logger),
):
    """
    Upload N files, extract, parse with GPT, compress & attach, and store records in Firestore.
    Emits per-file progress with `file_id` and overall progress with `file_id=None`.
    """
    client_id = request.session.get("client_id") or "Guest"
    user_id = current_user.get("userId")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID not found")

    # Start fresh progress state for this request context
    progress_logger.clear()

    # Group ID anchors this batch
    group_id = generate_group_id()

    # Pre-read files ONCE to keep loop non-blocking and avoid repeated I/O
    file_buffers: List[tuple[int, UploadFile, bytes]] = []
    for idx, f in enumerate(files):
        b = await f.read()
        await f.seek(0)
        if not b:
            logger.warning(f"Empty file: {getattr(f, 'filename', 'unknown')}")
        file_buffers.append((idx, f, b))

    # Run the parallel pipeline (limits concurrency internally)
    results = await process_files_in_parallel(
        file_buffers=file_buffers,
        user_id=user_id,
        group_id=group_id,
        progress_logger=progress_logger,
        # optional tuning: max_concurrency=min(4, (os.cpu_count() or 2)),
    )

    # Build metadata + compressed previews for successfully processed files
    files_metadata: List[Dict[str, str]] = []
    failed = 0

    # Use a dict keyed by index to line up with `results`
    for idx, (orig_idx, uf, raw) in enumerate(file_buffers):
        res = results.get(orig_idx)
        if not res:
            failed += 1
            # best-effort user log
            asyncio.create_task(send_log(f"❌ {uf.filename} failed to process.", user_room=client_id))
            continue

        parsed = res.get("parsed_content")
        if isinstance(parsed, dict) and "error" in parsed:
            failed += 1
            asyncio.create_task(
                send_log(
                    f'❌ {uf.filename} failed: {parsed.get("error","Unknown error")}. '
                    f"Please submit a valid invoice, bill, or receipt.",
                    user_room=client_id,
                )
            )
            continue

        sanitized_filename = res.get("sanitized_filename") or uf.filename

        # Attach a compressed preview (optional; you already had this)
        try:
            compression_stream = io.BytesIO(raw)
            compressed_file = await asyncio.to_thread(compress_file_blob, compression_stream, uf.filename)
            compressed_file.seek(0)
            content = compressed_file.read()
            encoded_content = base64.b64encode(content).decode("utf-8")
            files_metadata.append({"filename": sanitized_filename, "content": encoded_content})
        except Exception as e:
            logger.warning(f"Compression failed for {uf.filename}: {e}")

        asyncio.create_task(send_log(f"✅ {uf.filename} processed successfully!", user_room=client_id))

    if failed >= len(file_buffers):
        # Everyone failed → push to 100 overall and return error
        await sio.emit(EVENT_PROGRESS, {"progress": 100, "file_id": None}, room=client_id)
        return {"error": "❌ Files Processing Failed"}

    # Persist group metadata
    firestore_client = firestore.client()
    group_doc_ref = (
        firestore_client.collection("users")
        .document(user_id)
        .collection("groups")
        .document(group_id)
    )
    metadata = {
        "upload_time": datetime.now().isoformat(),
        "file_count": len(file_buffers) - failed,
        "group_name": group_id,
        "isViewed": False,
    }
    group_doc_ref.set({"metadata": metadata})
    if files_metadata:
        metadata["files"] = files_metadata
        group_doc_ref.set({"metadata": metadata}, merge=True)

    # Build the response object (files data + metadata)
    group_obj: Dict[str, Any] = {}
    for item in results.values():
        group_obj[item["sanitized_filename"]] = item["parsed_content"]
    group_obj["group_id"] = group_id
    group_obj["metadata"] = metadata

    # Ensure final 100% overall to close the UI bar if not already emitted
    await sio.emit(EVENT_PROGRESS, {"progress": 100, "file_id": None}, room=client_id)

    return group_obj


@router.post("/update-change-log")
async def update_change_log(
    request: Request,
    current_user: dict = Depends(get_current_user_from_cookie),
):
    user_id = current_user.get("userId")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User ID not found")

    try:
        payload = await request.json()
        change_log = payload.get("change_log")
        if change_log is None or not isinstance(change_log, list):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or missing change_log")

        client = await get_firestore_client()

        for record in change_log:
            action = record.get("action")
            path = record.get("path")
            if not action or not path:
                continue

            # Line item updates
            if LINE_ITEM_REGEX.match(f"users/{user_id}/{path}"):
                await handle_line_item_update(record, user_id)
                continue

            full_path = f"users/{user_id}/{path}"
            doc_ref = client.document(full_path)
            doc_snapshot = await document_get(doc_ref)
            exists = doc_snapshot.exists

            if action == "EDIT":
                await handle_document_edit(doc_ref, exists, record)
            elif action == "DELETE":
                await handle_document_delete(doc_ref, exists, record, user_id)
            elif action == "ADD":
                await handle_document_add(doc_ref, record)
            elif action == "GROUP-DELETE":
                await handle_group_delete(doc_ref, exists)
            else:
                continue

        return ok(content={"message": "Change log processed successfully."}, status_code=200)

    except Exception as e:
        logger.exception("Error processing change log")
        return ok({"error": str(e)}, status_code=500)


# /api/extractor/get-user-data?trashed=false|true|all
from fastapi import Query

from typing import Any, Dict, List, Literal
from fastapi import Query, HTTPException, status, Depends, Request
from datetime import datetime
import asyncio
import logging

# logger = logging.getLogger(__name__)

TrashMode = Literal["true", "false", "all"]

@router.get("/get-user-data")
async def get_user_data(
    request: Request,
    current_user: dict = Depends(get_current_user_from_cookie),
    trashed: TrashMode = Query("false", regex="^(true|false|all)$"),
    hide_empty_groups: bool = Query(False),
):
    user_id = current_user.get("userId")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID not found")

    try:
        client = await get_firestore_client()
        groups_ref = client.collection("users").document(user_id).collection("groups")

        # Fetch groups. Only pre-filter at DB when trashed=true (clear intent, faster).
        if trashed == "true":
            groups_q = groups_ref.where("metadata.trashed", "==", True)
            groups_docs = await asyncio.to_thread(lambda: list(groups_q.stream()))
        else:
            groups_docs = await asyncio.to_thread(lambda: list(groups_ref.stream()))

        async def fetch_group_data(group_doc):
            group_obj: Dict[str, Any] = {}
            group_data = group_doc.to_dict() or {}
            md: Dict[str, Any] = (group_data.get("metadata") or {}).copy()

            # If trashed=false, drop GROUPS that are trashed (but keep metadata.files intact)
            if trashed == "false" and bool(md.get("trashed", False)):
                return None

            group_obj["group_id"] = group_doc.id
            group_obj["metadata"] = md  # keep metadata.files — UI may depend on it

            files_ref = group_doc.reference.collection("files")

            # Load files subcollection
            if trashed == "true":
                # Only explicit trashed files
                files_docs = await asyncio.to_thread(lambda: list(files_ref.where("trashed", "==", True).stream()))
            else:
                # all files, we’ll filter in Python (needed for trashed=false/all & legacy docs)
                files_docs = await asyncio.to_thread(lambda: list(files_ref.stream()))

            kept = 0
            for file_doc in files_docs:
                file_data = file_doc.to_dict() or {}
                is_file_trashed = bool(file_data.get("trashed", False))

                if trashed == "false" and is_file_trashed:
                    # hide trashed files in normal view
                    continue
                # trashed == "all" includes everything; trashed == "true" was pre-filtered above

                # Ensure legacy docs without 'trashed' still show up in non-trash views
                group_obj[file_doc.id] = {
                    **file_data,
                    "trashed": is_file_trashed  # normalize presence
                }
                kept += 1

            # Optionally hide empty groups (default False so selections still resolve)
            if hide_empty_groups and kept == 0:
                return None

            return group_obj

        groups_list_raw = await asyncio.gather(*(fetch_group_data(doc) for doc in groups_docs))
        groups_list = [g for g in groups_list_raw if g is not None]

        # Sort by upload_time; for trashed=true prefer trashAt if present
        def sort_key(group):
            md = group.get("metadata", {}) if group else {}
            ts = md.get("trashAt") if trashed == "true" else md.get("upload_time")
            if not ts:
                return float("inf")
            try:
                dt = datetime.fromisoformat(ts)
                return abs((datetime.now() - dt).total_seconds())
            except Exception:
                return float("inf")

        sorted_groups = sorted(groups_list, key=sort_key)
        return JSONResponse(content={"uploads": sorted_groups}, status_code=200)

    except Exception:
        logger.exception("Failed to fetch user data")
        return JSONResponse(content={"error": "Failed to fetch user data."}, status_code=500)


@router.delete("/delete-group/{group_id}")
async def delete_group(
    group_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user_from_cookie),
):
    user_id = current_user.get("userId")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID not found")
    try:
        group_doc_ref = (
            await get_firestore_client()
        ).collection("users").document(user_id).collection("groups").document(group_id)
        group_snapshot = await document_get(group_doc_ref)
        if not group_snapshot.exists:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No group found with group_id: {group_id}")
        await document_delete(group_doc_ref)
        return JSONResponse(content={"message": f"Group {group_id} deleted successfully."}, status_code=200)
    except Exception:
        logger.exception(f"Error deleting group {group_id}")
        return ok({"error": "Failed to delete the group."}, status_code=500)


@router.delete("/delete/groups/{group_id}/files/{file_name}/records/{record_key}")
async def delete_record(
    group_id: str,
    file_name: str,
    record_key: str,
    request: Request,
    current_user: dict = Depends(get_current_user_from_cookie),
):
    if not group_id or not file_name or not record_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing required parameters")

    user_id = current_user.get("userId")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User ID not found")

    file_doc_ref = (
        await get_firestore_client()
    ).collection("users").document(user_id).collection("groups").document(group_id).collection("files").document(file_name)
    file_snapshot = await document_get(file_doc_ref)
    if not file_snapshot.exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")

    file_data = file_snapshot.to_dict() or {}
    if record_key not in file_data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found in file")

    await document_update(file_doc_ref, {record_key: admin_fs.DELETE_FIELD})

    updated_data = (await document_get(file_doc_ref)).to_dict() or {}
    if len(updated_data) == 0:
        await document_delete(file_doc_ref)

    files_ref = (
        await get_firestore_client()
    ).collection("users").document(user_id).collection("groups").document(group_id).collection("files")
    remaining_files = list(await stream_collection(files_ref))
    if len(remaining_files) == 0:
        group_doc_ref = (
            await get_firestore_client()
        ).collection("users").document(user_id).collection("groups").document(group_id)
        await document_delete(group_doc_ref)
        return ok(
            content={"message": f"Record {record_key} deleted. Group {group_id} also deleted."},
            status_code=200,
        )

    return ok(content={"message": f"Record {record_key} successfully deleted from {file_name}"}, status_code=200)


@router.put("/update/groups/{group_id}/files/{file_name}/records/{record_key}")
async def update_record(
    group_id: str,
    file_name: str,
    record_key: str,
    request: Request,
    current_user: dict = Depends(get_current_user_from_cookie),
):
    user_id = current_user.get("userId")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User ID not found")

    try:
        updated_data = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing request body")

    file_doc_ref = (
        await get_firestore_client()
    ).collection("users").document(user_id).collection("groups").document(group_id).collection("files").document(file_name)
    if not (await document_get(file_doc_ref)).exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")

    await document_update(file_doc_ref, updated_data)
    return JSONResponse(content={"message": f"Record {record_key} successfully updated."}, status_code=200)


@router.put("/update-group-name/{group_id}")
async def update_group_name(
    group_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user_from_cookie),
):
    user_id = current_user.get("userId")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID not found")

    try:
        payload = await request.json()
        new_group_name = payload.get("group_name")
        if not new_group_name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing group_name in request body")
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid request payload")

    client = await get_firestore_client()
    group_doc_ref = client.collection("users").document(user_id).collection("groups").document(group_id)
    group_snapshot = await document_get(group_doc_ref)
    if not group_snapshot.exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No group found with group_id: {group_id}")

    await document_update(group_doc_ref, {"metadata.group_name": new_group_name})
    return JSONResponse(
        content={"group_name": new_group_name, "message": f"Group name updated to {new_group_name} for {group_id}."},
        status_code=200,
    )


@router.delete("/delete-all-data/{email}")
async def delete_all_data(
    email: str,
    request: Request,
    current_user: dict = Depends(get_current_user_from_cookie),
):
    user_id = current_user.get("userId")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User ID not found")

    session_email = current_user.get("email")
    if not session_email:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email not found")

    if session_email.lower() != email.lower():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized: Email mismatch")

    firestore_client = await get_firestore_client()

    # Delete integrations data
    integrations_ref = firestore_client.collection("users").document(user_id).collection("integrations")
    integrations_snapshot = await document_get(integrations_ref)
    if integrations_snapshot and len(integrations_snapshot) > 0:
        try:
            for doc in integrations_snapshot:
                await document_delete(doc.reference)
        except Exception as e:
            raise HTTPException(status_code=500, detail="Failed to delete integrations data") from e

    # Delete groups data
    groups_ref = firestore_client.collection("users").document(user_id).collection("groups")
    groups_snapshot = await document_get(groups_ref)
    if groups_snapshot and len(groups_snapshot) > 0:
        try:
            for doc in groups_snapshot:
                await document_delete(doc.reference)
        except Exception as e:
            raise HTTPException(status_code=500, detail="Failed to delete groups data") from e

    return JSONResponse(content={"message": "All your data has been deleted successfully"}, status_code=200)


@router.patch("/update_view", status_code=200)
async def update_view_status(
    request: Request,
    current_user: dict = Depends(get_current_user_from_cookie),
):
    """
    Set metadata.isViewed=True for a given group (payload: {"groupId": "..."}).
    """
    user_id = current_user.get("userId")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID not found")

    try:
        payload = await request.json()
        group_id = payload.get("groupId")
        if not group_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing groupId in request body")
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid request payload")

    client = await get_firestore_client()
    group_doc_ref = client.collection("users").document(user_id).collection("groups").document(group_id)
    group_snapshot = await document_get(group_doc_ref)
    if not group_snapshot.exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    await document_update(group_doc_ref, {"metadata.isViewed": True})
    return JSONResponse(content={"message": "Group view status updated successfully."}, status_code=200)














from datetime import datetime, timedelta
from fastapi import Path

TRASH_TTL_DAYS = 30

def _now_ts():
    return datetime.utcnow()

def _ttl_ts(days: int = TRASH_TTL_DAYS):
    return _now_ts() + timedelta(days=days)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _ttl_utc_iso(days: int = TRASH_TTL_DAYS) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
# async def _group_ref(client, user_id: str, group_id: str):
#     return client.collection("users").document(user_id).collection("groups").document(group_id)
#
# async def _file_ref(client, user_id: str, group_id: str, file_name: str):
#     g = await _group_ref(client, user_id, group_id)
#     return g.collection("files").document(file_name)

# AFTER
# helpers (NO async, NO await)
def _group_ref(client, user_id: str, group_id: str):
    return client.collection("users").document(user_id).collection("groups").document(group_id)

def _file_ref(client, user_id: str, group_id: str, file_name: str):
    return _group_ref(client, user_id, group_id).collection("files").document(file_name)

async def _assert_exists(doc_ref, not_found_msg="Document not found"):
    snap = await document_get(doc_ref)
    if not snap.exists:
        raise HTTPException(status_code=404, detail=not_found_msg)
    return snap

# ──────────────────────────────────────────────────────────────────────────────
# Move GROUP to Trash (soft delete)
# ──────────────────────────────────────────────────────────────────────────────
@router.post("/trash/group/{group_id}")
async def trash_group(
    group_id: str = Path(...),
    request: Request = None,
    current_user: dict = Depends(get_current_user_from_cookie),
):
    user_id = current_user.get("userId")
    if not user_id: raise HTTPException(400, "User ID not found")

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    reason = body.get("reason") if isinstance(body, dict) else None

    client = await get_firestore_client()
    gref =  _group_ref(client, user_id, group_id)
    gsnap = await _assert_exists(gref, f"No group found: {group_id}")

    # in trash_group / trash_file:
    now = _now_utc_iso()
    exp = _ttl_utc_iso()

    # mark group trashed
    await document_update(gref, {
        "metadata.trashed": True,
        "metadata.trash_reason": reason or "user_action",
        "metadata.trashAt": now,
        "metadata.trashExpiresAt": exp,
    })

    # mark all files trashed (and give them TTL too)
    files_ref = gref.collection("files")
    files = list(await stream_collection(files_ref))
    for fdoc in files:
        await document_update(fdoc.reference, {
            "trashed": True,
            "trash_reason": reason or "user_action",
            "trashAt": now,
            "trashExpiresAt": exp,
        })

    return {"message": f"Group {group_id} moved to Trash until {exp.format()}."}

# ──────────────────────────────────────────────────────────────────────────────
# Move FILE to Trash (soft delete)
# ──────────────────────────────────────────────────────────────────────────────
@router.post("/trash/file/{group_id}/{file_name}")
async def trash_file(
    group_id: str,
    file_name: str,
    request: Request,
    current_user: dict = Depends(get_current_user_from_cookie),
):
    user_id = current_user.get("userId")
    if not user_id: raise HTTPException(400, "User ID not found")

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    reason = body.get("reason") if isinstance(body, dict) else None

    client = await get_firestore_client()
    fref =  _file_ref(client, user_id, group_id, file_name)
    await _assert_exists(fref, "File not found")

    now = _now_ts()
    exp = _ttl_ts()

    await document_update(fref, {
        "trashed": True,
        "trash_reason": reason or "user_action",
        "trashAt": now,
        "trashExpiresAt": exp,
    })

    # Optionally mark group trashed if ALL files are trashed:
    # (Keep your current behavior; here we do NOT auto-trash the group.)

    return {"message": f"File {file_name} moved to Trash until {exp.isoformat()}."}

# ──────────────────────────────────────────────────────────────────────────────
# Restore GROUP from Trash
# ──────────────────────────────────────────────────────────────────────────────
@router.post("/trash/restore/group/{group_id}")
async def restore_group(
    group_id: str,
    current_user: dict = Depends(get_current_user_from_cookie),
):
    user_id = current_user.get("userId")
    if not user_id: raise HTTPException(400, "User ID not found")

    client = await get_firestore_client()
    gref =  _group_ref(client, user_id, group_id)
    await _assert_exists(gref, "Group not found")

    # untrash group
    await document_update(gref, {
        "metadata.trashed": False,
        "metadata.trash_reason": admin_fs.DELETE_FIELD,
        "metadata.trashAt": admin_fs.DELETE_FIELD,
        "metadata.trashExpiresAt": admin_fs.DELETE_FIELD,
    })

    # untrash files under group
    files_ref = gref.collection("files")
    files = list(await stream_collection(files_ref))
    for fdoc in files:
        await document_update(fdoc.reference, {
            "trashed": False,
            "trash_reason": admin_fs.DELETE_FIELD,
            "trashAt": admin_fs.DELETE_FIELD,
            "trashExpiresAt": admin_fs.DELETE_FIELD,
        })

    return {"message": f"Group {group_id} restored from Trash."}

# ──────────────────────────────────────────────────────────────────────────────
# Restore FILE from Trash
# ──────────────────────────────────────────────────────────────────────────────
# /trash/restore/file/{group_id}/{file_name}
@router.post("/trash/restore/file/{group_id}/{file_name}")
async def restore_file(
    group_id: str,
    file_name: str,
    current_user: dict = Depends(get_current_user_from_cookie),
):
    user_id = current_user.get("userId")
    if not user_id: raise HTTPException(400, "User ID not found")

    client = await get_firestore_client()
    fref = _file_ref(client, user_id, group_id, file_name)
    await _assert_exists(fref, "File not found")

    # 1) restore the file
    await document_update(fref, {
        "trashed": False,
        "trash_reason": admin_fs.DELETE_FIELD,
        "trashAt": admin_fs.DELETE_FIELD,
        "trashExpiresAt": admin_fs.DELETE_FIELD,
    })

    # 2) ensure the group is active if it was trashed
    gref = _group_ref(client, user_id, group_id)
    gsnap = await document_get(gref)
    if gsnap.exists:
        gmd = (gsnap.to_dict() or {}).get("metadata", {}) or {}
        if gmd.get("trashed"):
            await document_update(gref, {
                "metadata.trashed": False,
                "metadata.trash_reason": admin_fs.DELETE_FIELD,
                "metadata.trashAt": admin_fs.DELETE_FIELD,
                "metadata.trashExpiresAt": admin_fs.DELETE_FIELD,
            })

    return {"message": f"File {file_name} restored from Trash."}


# ──────────────────────────────────────────────────────────────────────────────
# Permanently delete GROUP
# ──────────────────────────────────────────────────────────────────────────────
@router.delete("/trash/permanent/group/{group_id}")
async def purge_group_permanently(
    group_id: str,
    current_user: dict = Depends(get_current_user_from_cookie),
):
    user_id = current_user.get("userId")
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID not found")

    client = await get_firestore_client()
    gref = _group_ref(client, user_id, group_id)
    gsnap = await document_get(gref)
    if not gsnap.exists:
        raise HTTPException(status_code=404, detail="Group not found")

    gmd = (gsnap.to_dict() or {}).get("metadata", {}) or {}
    is_group_trashed = bool(gmd.get("trashed"))

    files_ref = gref.collection("files")

    if is_group_trashed:
        # Group itself is trashed → delete EVERYTHING
        fdocs = await asyncio.to_thread(lambda: list(files_ref.stream()))
        for fdoc in fdocs:
            await document_delete(fdoc.reference)
        await document_delete(gref)
        return {"message": f"Group {group_id} permanently deleted (group was trashed)."}
    else:
        # Group is NOT trashed → delete ONLY trashed files, keep the rest
        fdocs = await asyncio.to_thread(lambda: list(
            files_ref.where(filter=FieldFilter("trashed", "==", True)).stream()
        ))
        for fdoc in fdocs:
            await document_delete(fdoc.reference)

        # Optional: if nothing remains, delete group too
        remaining = await asyncio.to_thread(lambda: list(files_ref.stream()))
        if not remaining:
            await document_delete(gref)

        return {"message": f"Deleted trashed files in group {group_id}. Group kept."}


# ──────────────────────────────────────────────────────────────────────────────
# Permanently delete FILE
# ──────────────────────────────────────────────────────────────────────────────
@router.delete("/trash/permanent/file/{group_id}/{file_name}")
async def purge_file_permanently(
    group_id: str,
    file_name: str,
    current_user: dict = Depends(get_current_user_from_cookie),
):
    user_id = current_user.get("userId")
    if not user_id: raise HTTPException(400, "User ID not found")

    client = await get_firestore_client()
    fref =  _file_ref(client, user_id, group_id, file_name)
    await _assert_exists(fref, "File not found")
    await document_delete(fref)

    # if group becomes empty, optionally delete it too
    gref =  _group_ref(client, user_id, group_id)
    remaining = list(await stream_collection(gref.collection("files")))
    if len(remaining) == 0:
        await document_delete(gref)
        return {"message": f"File {file_name} deleted. Group {group_id} was empty and has been deleted."}

    return {"message": f"File {file_name} permanently deleted."}

# ──────────────────────────────────────────────────────────────────────────────
# List Trash (groups & files with time left)
# ──────────────────────────────────────────────────────────────────────────────
# routes/data_extractor_routes.py
# top of file
from datetime import datetime, timezone
from typing import Optional, Any, Dict, List

# ── time helpers ──────────────────────────────────────────────────────────────
def _to_utc_aware(dt_like: Optional[object]) -> Optional[datetime]:
    """Accept Firestore Timestamp, datetime, or ISO string. Return UTC-aware datetime."""
    if dt_like is None:
        return None
    if hasattr(dt_like, "to_datetime"):  # Firestore Timestamp
        try:
            d = dt_like.to_datetime()
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return d.astimezone(timezone.utc)
        except Exception:
            return None
    if isinstance(dt_like, datetime):
        d = dt_like
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    if isinstance(dt_like, str):
        s = dt_like.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            d = datetime.fromisoformat(s)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return d.astimezone(timezone.utc)
        except Exception:
            return None
    return None

def _days_left(expiry_like: Optional[object]) -> Optional[int]:
    exp = _to_utc_aware(expiry_like)
    if not exp:
        return None
    now = datetime.now(timezone.utc)
    secs = (exp - now).total_seconds()
    return max(0, int(secs // 86400))
def _to_iso(dt_like):
    d = _to_utc_aware(dt_like)
    return d.isoformat() if d else None
# ── endpoint ─────────────────────────────────────────────────────────────────
@router.get("/trash")
async def list_trash(current_user: dict = Depends(get_current_user_from_cookie)):
    """
    Full trash list grouped by group_id:
      • Trashed groups (with trashed files)
      • Trashed files inside non-trashed groups
    """
    user_id = current_user.get("userId")
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID not found")

    client = await get_firestore_client()
    user_ref = client.collection("users").document(user_id)
    groups_ref = user_ref.collection("groups")

    payload_by_gid: Dict[str, Any] = {}

    # 1) Trashed groups + their trashed files
    trashed_groups = await asyncio.to_thread(
        lambda: list(groups_ref.where(filter=FieldFilter("metadata.trashed", "==", True)).stream())
    )
    for gdoc in trashed_groups:
        gdata = gdoc.to_dict() or {}
        md = gdata.get("metadata", {}) or {}
        entry = {
            "type": "group",
            "group_id": gdoc.id,
            "group_name": md.get("group_name", gdoc.id),
            "trashed": True,
            "trashAt": _to_iso(md.get("trashAt")),
            "trashExpiresAt": _to_iso(md.get("trashExpiresAt")),
            "upload_time": _to_iso(md.get("upload_time")),
            "daysLeft": _days_left(md.get("trashExpiresAt")),
            "files": [],
        }
        files_q = gdoc.reference.collection("files").where(filter=FieldFilter("trashed", "==", True))
        files = await asyncio.to_thread(lambda: list(files_q.stream()))
        for fdoc in files:
            fdata = fdoc.to_dict() or {}
            entry["files"].append({
                "file_name": fdoc.id,
                "trashed": True,
                  "trashAt": _to_iso(fdata.get("trashAt")),
    "trashExpiresAt": _to_iso(fdata.get("trashExpiresAt")),
                "daysLeft": _days_left(fdata.get("trashExpiresAt")),
            })
        payload_by_gid[gdoc.id] = entry

    # 2) Trashed files in non-trashed groups (collection-group with fallback)
    try:
        files_cg = client.collection_group("files").where(filter=FieldFilter("trashed", "==", True))
        trashed_files_cg = await asyncio.to_thread(lambda: list(files_cg.stream()))
    except FailedPrecondition:
        # Index not built yet → fallback per-group scan
        all_groups = await asyncio.to_thread(lambda: list(groups_ref.stream()))
        trashed_files_cg = []
        for gdoc in all_groups:
            fq = gdoc.reference.collection("files").where(filter=FieldFilter("trashed", "==", True))
            trashed_files_cg.extend(await asyncio.to_thread(lambda q=fq: list(q.stream())))

    for fdoc in trashed_files_cg:
        # Expect: users/{uid}/groups/{gid}/files/{fid}
        parts = fdoc.reference.path.split("/")
        if len(parts) < 6 or parts[0] != "users" or parts[2] != "groups" or parts[4] != "files":
            continue
        uid, gid = parts[1], parts[3]
        if uid != user_id:
            continue

        if gid not in payload_by_gid:
            gsnap = await document_get(groups_ref.document(gid))
            gmd = (gsnap.to_dict() or {}).get("metadata", {}) if gsnap.exists else {}
            payload_by_gid[gid] = {
                "type": "group",
                "group_id": gid,
                "group_name": gmd.get("group_name", gid),
                "trashed": bool(gmd.get("trashed", False)),
                "trashAt": _to_iso(gmd.get("trashAt")),
                "trashExpiresAt": _to_iso(gmd.get("trashExpiresAt")),
                "upload_time": _to_iso(gmd.get("upload_time")),
                "daysLeft": _days_left(gmd.get("trashExpiresAt")) if gmd.get("trashed") else None,
                "files": [],
            }

        fdata = fdoc.to_dict() or {}
        if not any(x["file_name"] == fdoc.id for x in payload_by_gid[gid]["files"]):
            payload_by_gid[gid]["files"].append({
                "file_name": fdoc.id,
                "trashed": True,
                "trashAt": _to_iso(fdata.get("trashAt")),
                "trashExpiresAt": _to_iso(fdata.get("trashExpiresAt")),
                "daysLeft": _days_left(fdata.get("trashExpiresAt")),
            })

    # Sort by trashAt desc, fallback to upload_time
    def parse_dt(entry: Dict[str, Any]) -> datetime:
        d = _to_utc_aware(entry.get("trashAt")) or _to_utc_aware(entry.get("upload_time"))
        return d or datetime.min.replace(tzinfo=timezone.utc)

    payload: List[Dict[str, Any]] = list(payload_by_gid.values())
    payload.sort(key=parse_dt, reverse=True)
    return {"trash": payload}






















# import asyncio
# from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

# from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, validator

# from firebase_admin import firestore as admin_fs  # for DELETE_FIELD
# from google.cloud import firestore  # client, SERVER_TIMESTAMP

# from masyg_extractor.config.jwt_config import get_current_user_from_cookie
# from masyg_extractor.services.firestore_helpers import get_firestore_client, document_get

# … your existing router = APIRouter(prefix="/extractor")


# ──────────────────────────────────────────────────────────────────────────────
# Models
# ──────────────────────────────────────────────────────────────────────────────
class BulkFileItem(BaseModel):
    groupId: str = Field(..., min_length=1)
    fileId: str = Field(..., min_length=1)

class BulkRequest(BaseModel):
    groups: List[str] = []
    files: List[BulkFileItem] = []
    # trash-only option: also mark all files inside trashed groups as trashed
    cascade_files: bool = True

    @validator("groups", each_item=True)
    def _strip_groups(cls, v: str) -> str:
        return v.strip()

class BulkResultEntry(BaseModel):
    status: str  # "ok" | "error"
    error: Optional[str] = None

class BulkGroupResult(BulkResultEntry):
    id: str

class BulkFileResult(BulkResultEntry):
    groupId: str
    fileId: str

class BulkResponse(BaseModel):
    ok: bool = True
    results: Dict[str, List[Dict[str, Any]]]  # {"groups":[...], "files":[...]}


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
BATCH_LIMIT = 450  # stay safely under Firestore’s 500 write limit
RETENTION_DAYS = 30

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

def _trash_expiry() -> datetime:
    return _utcnow() + timedelta(days=RETENTION_DAYS)

def _chunks[T](seq: List[T], size: int) -> List[List[T]]:
    return [seq[i:i+size] for i in range(0, len(seq), size)]

# def _group_ref(client: firestore.Client, user_id: str, gid: str):
#     return client.collection("users").document(user_id).collection("groups").document(gid)
#
# def _file_ref(client: firestore.Client, user_id: str, gid: str, fid: str):
#     return _group_ref(client, user_id, gid).collection("files").document(fid)

async def _commit_batch(batch: firestore.WriteBatch):
    # Commit in a thread to avoid blocking the loop
    await asyncio.to_thread(batch.commit)

async def _write_in_chunks(client: firestore.Client, ops: List[Tuple[str, Any, Dict[str, Any]]]) -> List[Tuple[Any, Optional[str]]]:
    """
    ops: list of (kind, ref, data) where kind in {"update","set","delete"}
    returns: list of (ref, error_message_or_None)
    """
    results: List[Tuple[Any, Optional[str]]] = []
    for slice_ops in _chunks(ops, BATCH_LIMIT):
        batch = client.batch()
        for kind, ref, data in slice_ops:
            try:
                if kind == "update":
                    batch.update(ref, data)
                elif kind == "set":
                    batch.set(ref, data, merge=True)
                elif kind == "delete":
                    batch.delete(ref)
            except Exception as e:
                # record client-side assembly error immediately
                results.append((ref, str(e)))
        try:
            await _commit_batch(batch)
            for kind, ref, data in slice_ops:
                results.append((ref, None))
        except Exception as e:
            # mark all in the slice as failed (server-side error)
            err = str(e)
            for kind, ref, data in slice_ops:
                results.append((ref, err))
    return results

async def _delete_group_tree(client: firestore.Client, user_id: str, gid: str) -> Optional[str]:
    """
    Deletes all files under a group then the group doc. Returns error string or None.
    """
    try:
        gref = _group_ref(client, user_id, gid)
        gsnap = await document_get(gref)
        if not gsnap.exists:
            return None  # already gone

        # collect file deletes
        files_ref = gref.collection("files")
        file_docs = await asyncio.to_thread(lambda: list(files_ref.stream()))
        ops: List[Tuple[str, Any, Dict[str, Any]]] = []
        for fdoc in file_docs:
            ops.append(("delete", fdoc.reference, {}))
        # add group delete
        ops.append(("delete", gref, {}))
        res = await _write_in_chunks(client, ops)

        # summarize any errors
        err = next((e for (_ref, e) in res if e), None)
        return err
    except Exception as e:
        return str(e)







# from __future__ import annotations
#
# import asyncio
# from datetime import datetime, timezone, timedelta
# from typing import Any, Dict, List
#
# from fastapi import APIRouter, Depends, HTTPException, Request
# from firebase_admin import firestore as admin_fs
# from google.cloud.firestore_v1.base_query import FieldFilter
#
# from masyg_extractor.services.firestore_helpers import (
#     get_firestore_client, document_get, document_update, document_delete
# )
# from masyg_extractor.config.jwt_config import get_current_user_from_cookie
#
# router = APIRouter(prefix="/extractor")

TRASH_TTL_DAYS = 30

def _utc_now():
    return datetime.now(timezone.utc)

# def _trash_expiry():
#     return _utc_now() + timedelta(days=TRASH_TTL_DAYS)

@router.post("/trash/bulk")
async def bulk_trash(
    request: Request,
    current_user: dict = Depends(get_current_user_from_cookie),
):
    """
    Body:
      {
        "groups": ["gid1","gid2",...],
        "files": [{"groupId":"gid","fileId":"fid"}, ...],
        "cascade_files": true|false
      }
    """
    user_id = current_user.get("userId")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID not found")

    body = await request.json()
    groups: List[str] = body.get("groups", []) or []
    files: List[Dict[str, str]] = body.get("files", []) or []
    cascade_files: bool = bool(body.get("cascade_files", True))

    client = await get_firestore_client()
    groups_ref = client.collection("users").document(user_id).collection("groups")

    results = {"groups": [], "files": []}

    # ---- Trash groups (optionally cascade to files)
    for gid in groups:
        try:
            gref = groups_ref.document(gid)              # <-- DocumentReference
            gsnap = await document_get(gref)             # <-- Snapshot (awaited)
            if not gsnap.exists:
                results["groups"].append({"groupId": gid, "status": "error", "error": "not_found"})
                continue

            now = _utc_now().isoformat()
            exp = _trash_expiry().isoformat()
            await document_update(gref, {
                "metadata.trashed": True,
                "metadata.trashAt": now,
                "metadata.trashExpiresAt": exp,
            })

            if cascade_files:
                fq = gref.collection("files")
                fdocs = await asyncio.to_thread(lambda: list(fq.stream()))
                for fdoc in fdocs:
                    fref = fq.document(fdoc.id)
                    await document_update(fref, {
                        "trashed": True,
                        "trashAt": now,
                        "trashExpiresAt": exp,
                    })

            results["groups"].append({"groupId": gid, "status": "ok"})
        except Exception as e:
            results["groups"].append({"groupId": gid, "status": "error", "error": str(e)})

    # ---- Trash individual files
    for item in files:
        gid, fid = item.get("groupId"), item.get("fileId")
        if not gid or not fid:
            results["files"].append({"groupId": gid, "fileId": fid, "status": "error", "error": "bad_args"})
            continue
        try:
            fref = groups_ref.document(gid).collection("files").document(fid)  # <-- chain refs
            fsnap = await document_get(fref)
            if not fsnap.exists:
                results["files"].append({"groupId": gid, "fileId": fid, "status": "error", "error": "not_found"})
                continue
            #integrations_ref
            now = _utc_now().isoformat()
            exp = _trash_expiry().isoformat()
            await document_update(fref, {
                "trashed": True,
                "trashAt": now,
                "trashExpiresAt": exp,
            })
            results["files"].append({"groupId": gid, "fileId": fid, "status": "ok"})
        except Exception as e:
            results["files"].append({"groupId": gid, "fileId": fid, "status": "error", "error": str(e)})

    return {"results": results}


# top of file
from typing import Set

@router.post("/trash/restore/bulk")
async def bulk_restore(
    request: Request,
    current_user: dict = Depends(get_current_user_from_cookie),
):
    user_id = current_user.get("userId")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID not found")

    body = await request.json()
    groups: List[str] = body.get("groups", []) or []
    files: List[Dict[str, str]] = body.get("files", []) or []
    cascade_files: bool = bool(body.get("cascade_files", True))

    client = await get_firestore_client()
    groups_ref = client.collection("users").document(user_id).collection("groups")

    results = {"groups": [], "files": []}

    # ---- Restore groups (cascade only if explicitly requested)
    for gid in groups:
        try:
            gref = groups_ref.document(gid)
            gsnap = await document_get(gref)
            if not gsnap.exists:
                results["groups"].append({"groupId": gid, "status": "error", "error": "not_found"})
                continue

            await document_update(gref, {
                "metadata.trashed": False,
                "metadata.trash_reason": admin_fs.DELETE_FIELD,
                "metadata.trashAt": admin_fs.DELETE_FIELD,
                "metadata.trashExpiresAt": admin_fs.DELETE_FIELD,
            })

            if cascade_files:
                fq = gref.collection("files").where(filter=FieldFilter("trashed", "==", True))
                fdocs = await asyncio.to_thread(lambda: list(fq.stream()))
                for fdoc in fdocs:
                    await document_update(fdoc.reference, {
                        "trashed": False,
                        "trash_reason": admin_fs.DELETE_FIELD,
                        "trashAt": admin_fs.DELETE_FIELD,
                        "trashExpiresAt": admin_fs.DELETE_FIELD,
                    })

            results["groups"].append({"groupId": gid, "status": "ok"})
        except Exception as e:
            results["groups"].append({"groupId": gid, "status": "error", "error": str(e)})

    # ---- Restore individual files (NEVER cascade)
    touched_groups: Set[str] = set()
    for item in files:
        gid, fid = item.get("groupId"), item.get("fileId")
        if not gid or not fid:
            results["files"].append({"groupId": gid, "fileId": fid, "status": "error", "error": "bad_args"})
            continue
        try:
            fref = groups_ref.document(gid).collection("files").document(fid)
            fsnap = await document_get(fref)
            if not fsnap.exists:
                results["files"].append({"groupId": gid, "fileId": fid, "status": "error", "error": "not_found"})
                continue

            await document_update(fref, {
                "trashed": False,
                "trash_reason": admin_fs.DELETE_FIELD,
                "trashAt": admin_fs.DELETE_FIELD,
                "trashExpiresAt": admin_fs.DELETE_FIELD,
            })
            results["files"].append({"groupId": gid, "fileId": fid, "status": "ok"})
            touched_groups.add(gid)
        except Exception as e:
            results["files"].append({"groupId": gid, "fileId": fid, "status": "error", "error": str(e)})

    # ---- Ensure parent groups are visible (but DO NOT touch sibling files)
    for gid in touched_groups:
        try:
            gref = groups_ref.document(gid)
            gsnap = await document_get(gref)
            if gsnap.exists:
                gmd = (gsnap.to_dict() or {}).get("metadata", {}) or {}
                if gmd.get("trashed"):
                    await document_update(gref, {
                        "metadata.trashed": False,
                        "metadata.trash_reason": admin_fs.DELETE_FIELD,
                        "metadata.trashAt": admin_fs.DELETE_FIELD,
                        "metadata.trashExpiresAt": admin_fs.DELETE_FIELD,
                    })
        except Exception:
            # non-fatal; file restore already done
            pass

    return {"results": results}




@router.post("/trash/permanent/bulk")
async def bulk_permanent(
    request: Request,
    current_user: dict = Depends(get_current_user_from_cookie),
):
    user_id = current_user.get("userId")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID not found")

    body = await request.json()
    groups: List[str] = body.get("groups", []) or []
    files: List[Dict[str, str]] = body.get("files", []) or []
    cascade_files: bool = bool(body.get("cascade_files", True))

    client = await get_firestore_client()
    groups_ref = client.collection("users").document(user_id).collection("groups")

    results = {"groups": [], "files": []}

    # ---- Permanently delete groups (optionally their files)
    # ---- Permanently delete groups (respect group trashed state)
    for gid in groups:
        try:
            gref = groups_ref.document(gid)
            gsnap = await document_get(gref)
            if not gsnap.exists:
                results["groups"].append({"groupId": gid, "status": "error", "error": "not_found"})
                continue

            gmd = (gsnap.to_dict() or {}).get("metadata", {}) or {}
            is_group_trashed = bool(gmd.get("trashed"))

            files_ref = gref.collection("files")

            if is_group_trashed:
                # delete entire tree
                fdocs = await asyncio.to_thread(lambda: list(files_ref.stream()))
                for fdoc in fdocs:
                    await document_delete(fdoc.reference)
                await document_delete(gref)
                results["groups"].append({"groupId": gid, "status": "ok", "note": "group-trashed: deleted-all"})
            else:
                # delete ONLY trashed files, keep others & keep group
                fdocs = await asyncio.to_thread(lambda: list(
                    files_ref.where(filter=FieldFilter("trashed", "==", True)).stream()
                ))
                for fdoc in fdocs:
                    await document_delete(fdoc.reference)

                # Optional: if empty now, delete group too
                remaining = await asyncio.to_thread(lambda: list(files_ref.stream()))
                if not remaining:
                    await document_delete(gref)
                results["groups"].append({"groupId": gid, "status": "ok", "note": "deleted-only-trashed-files"})
        except Exception as e:
            results["groups"].append({"groupId": gid, "status": "error", "error": str(e)})

    # ---- Permanently delete individual files
    for item in files:
        gid, fid = item.get("groupId"), item.get("fileId")
        if not gid or not fid:
            results["files"].append({"groupId": gid, "fileId": fid, "status": "error", "error": "bad_args"})
            continue
        try:
            fref = groups_ref.document(gid).collection("files").document(fid)
            fsnap = await document_get(fref)
            if not fsnap.exists:
                results["files"].append({"groupId": gid, "fileId": fid, "status": "error", "error": "not_found"})
                continue
            await document_delete(fref)
            results["files"].append({"groupId": gid, "fileId": fid, "status": "ok"})
        except Exception as e:
            results["files"].append({"groupId": gid, "fileId": fid, "status": "error", "error": str(e)})

    return {"results": results}

