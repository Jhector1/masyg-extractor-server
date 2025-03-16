from fastapi import APIRouter, Request, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from re import compile
from firebase_admin import firestore as admin_fs
from masyg_extractor.services.firestore_helpers import (
    get_firestore_client,
    document_get,
    document_set,
    document_update,
    document_delete,
)
from masyg_extractor.services.dependencies import get_firebase_user
from masyg_extractor.services.my_log import send_log, logger

router = APIRouter(prefix="/extractor")

LINE_ITEM_REGEX = compile(r'^(?P<base_path>.+)/line_items/(?P<index>\d+)$')


async def handle_line_item_update(record: dict, user_id: str) -> None:
    """
    Handles updates for line items within a document.
    """
    path = f"users/{user_id}/{record.get('path')}"
    field = record.get('field')
    new_value = record.get('newValue')
    action = record.get('action')

    match = LINE_ITEM_REGEX.match(path)
    if not match:
        return  # Not a line item update

    base_path = match.group('base_path')
    try:
        line_item_index = int(match.group('index'))
    except Exception:
        return

    client = await get_firestore_client()
    doc_ref = client.document(base_path)
    doc_snapshot = await document_get(doc_ref)
    if not doc_snapshot.exists:
        await document_set(doc_ref, {"line_items": []})
        doc_snapshot = await document_get(doc_ref)
    doc_data = doc_snapshot.to_dict() or {}
    line_items = doc_data.get('line_items', [])

    # Add new item if index is out of range and action is EDIT/ADD without a specific field.
    if line_item_index < 0 or line_item_index >= len(line_items):
        if action in ['EDIT', 'ADD'] and (not field) and isinstance(new_value, dict):
            line_items.append(new_value)
            line_item_index = len(line_items) - 1
        else:
            return

    # Process actions
    if action == 'EDIT':
        if field:
            line_items[line_item_index][field] = new_value
        elif isinstance(new_value, dict):
            line_items[line_item_index].update(new_value)
    elif action == 'DELETE':
        if field:
            if field in line_items[line_item_index]:
                del line_items[line_item_index][field]
        else:
            line_items.pop(line_item_index)
    elif action == 'ADD' and isinstance(new_value, dict):
        line_items[line_item_index].update(new_value)

    await document_update(doc_ref, {"line_items": line_items})


async def handle_document_edit(doc_ref, doc_exists: bool, record: dict) -> None:
    """
    Handles EDIT actions on documents.
    """
    field = record.get('field')
    new_value = record.get('newValue')
    if not doc_exists:
        if field:
            await document_set(doc_ref, {field: new_value})
        elif isinstance(new_value, dict):
            await document_set(doc_ref, new_value)
    else:
        if field:
            await document_update(doc_ref, {field: new_value})
        elif isinstance(new_value, dict):
            await document_set(doc_ref, new_value, merge=True)


async def handle_document_delete(doc_ref, doc_exists: bool, record: dict, user_id: str) -> None:
    """
    Handles DELETE actions on documents.
    """
    field = record.get('field')
    path = record.get('path')
    if field:
        await document_update(doc_ref, {field: admin_fs.DELETE_FIELD})
    else:
        parts = f"users/{user_id}/{path}".split('/')
        if len(parts) >= 6:
            group_id = parts[3]
            file_to_delete = parts[5]
            if doc_exists:
                await document_delete(doc_ref)
            # Update group metadata
            client = await get_firestore_client()
            group_doc_path = f"users/{user_id}/groups/{group_id}"
            group_doc_ref = client.document(group_doc_path)
            group_snapshot = await document_get(group_doc_ref)
            if group_snapshot.exists:
                group_data = group_snapshot.to_dict() or {}
                metadata = group_data.get("metadata", {})
                files_metadata = metadata.get("files", [])
                new_files_metadata = [f for f in files_metadata if f.get("filename") != file_to_delete]
                if not new_files_metadata:
                    await document_delete(group_doc_ref)
                else:
                    metadata["files"] = new_files_metadata
                    metadata["file_count"] = len(new_files_metadata)
                    await document_update(group_doc_ref, {"metadata": metadata})
        else:
            if doc_exists:
                await document_delete(doc_ref)


async def handle_document_add(doc_ref, record: dict) -> None:
    """
    Handles ADD actions on documents.
    """
    new_value = record.get('newValue')
    if isinstance(new_value, dict):
        await document_set(doc_ref, new_value, merge=True)


async def handle_group_delete(doc_ref, doc_exists: bool) -> None:
    """
    Handles group deletion.
    """
    if doc_exists:
        await document_delete(doc_ref)


