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

        return JSONResponse(content={"message": "Change log processed successfully."}, status_code=200)

    except Exception as e:
        logger.exception("Error processing change log")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.get("/get-user-data")
async def get_user_data(
    request: Request,
    current_user: dict = Depends(get_current_user_from_cookie),
):
    user_id = current_user.get("userId")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID not found")

    try:
        firestore_client = await get_firestore_client()
        groups_ref = firestore_client.collection("users").document(user_id).collection("groups")

        groups_docs = await asyncio.to_thread(lambda: list(groups_ref.stream()))

        async def fetch_group_data(group_doc):
            group_obj: Dict[str, Any] = {}
            group_data = group_doc.to_dict() or {}
            group_obj["group_id"] = group_doc.id
            group_obj["metadata"] = group_data.get("metadata", {})

            files_ref = group_doc.reference.collection("files")
            file_docs = await asyncio.to_thread(lambda: list(files_ref.stream()))
            for file_doc in file_docs:
                group_obj[file_doc.id] = file_doc.to_dict()
            return group_obj

        groups_list = await asyncio.gather(*(fetch_group_data(doc) for doc in groups_docs))

        def sort_key(group):
            uts = group.get("metadata", {}).get("upload_time")
            if not uts:
                return float("inf")
            try:
                dt = datetime.fromisoformat(uts)
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
        return JSONResponse(content={"error": "Failed to delete the group."}, status_code=500)


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
        return JSONResponse(
            content={"message": f"Record {record_key} deleted. Group {group_id} also deleted."},
            status_code=200,
        )

    return JSONResponse(content={"message": f"Record {record_key} successfully deleted from {file_name}"}, status_code=200)


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
