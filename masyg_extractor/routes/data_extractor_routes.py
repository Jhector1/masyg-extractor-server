import asyncio
import io
import base64
import logging
from datetime import datetime
from typing import List

from fastapi import APIRouter, Request, UploadFile, File, Depends, status


from firebase_admin import firestore, firestore as admin_fs

from masyg_extractor.services.change_log_services import handle_document_edit, handle_document_delete, \
    handle_document_add, LINE_ITEM_REGEX, handle_line_item_update, handle_group_delete
from masyg_extractor.services.processing import process_files_in_parallel
from masyg_extractor.services.image_extractor_service import compress_file_blob
from masyg_extractor.services.firestore_helpers import (
    get_firestore_client,
    document_get,
    document_update,
    document_delete,
    stream_collection,
)
from masyg_extractor.utils.extensions import sio
from masyg_extractor.services.dependencies import get_firebase_user, generate_group_id
from masyg_extractor.services.my_log import send_log, logger

router = APIRouter(prefix="/extractor")

@router.post("/extract-data", status_code=status.HTTP_201_CREATED)
async def extract_data(
    request: Request,
    files: List[UploadFile] = File(...),
    firebase_user: dict = Depends(get_firebase_user)
):


    client_id = request.session.get("client_id")
    if client_id is None:
        client_id ='Guest'
    print('fffffffff',client_id)
    user_id = firebase_user.get('userId')
    await sio.emit("progress_update", {"progress": 10}, room=client_id)
    await asyncio.sleep(0.2)  # simulate a short wait

    # print(user_id)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User ID not found"
        )

    # asyncio.create_task(send_log("↗️ Request Received", user_room=client_id))
    # await sio.emit('log_message', {'data': '✅ Request Received'}, room=client_id)

    group_id = generate_group_id()

    failed_file_quant =0

    # Read file bytes once and log file sizes.
    file_contents = {file.filename: await file.read() for file in files}
    for filename, data in file_contents.items():
        logging.debug(f"File {filename} size: {len(data)} bytes")

    results = await process_files_in_parallel(files, user_id, group_id, client_id)
    print(results)

    # Compress and encode files.
    files_metadata = []
    for file in files:
        result = results.get(files.index(file), {})
        if 'error' in result['parsed_content']:
            asyncio.create_task(send_log(f'❌ {file.filename} failed  to process. Please submit a valid invoice, bill, or receipt', user_room=client_id))
            failed_file_quant+=1
            continue
        else:
            asyncio.create_task(
                send_log(f'✅ {file.filename} process successfully!',
                         user_room=client_id))

        sanitized_filename = result.get('sanitized_filename')
        compression_stream = io.BytesIO(file_contents[file.filename]) #process
        compressed_file = await asyncio.to_thread(compress_file_blob, compression_stream, file.filename)
        compressed_file.seek(0)
        content = compressed_file.read()
        encoded_content = base64.b64encode(content).decode('utf-8')
        files_metadata.append({
            'filename': sanitized_filename,
            'content': encoded_content
        })
    if failed_file_quant >= len(files):
        return {'error': '❌Files Processing Failed'}

    group_doc_ref = firestore.client().collection("users").document(user_id) \
        .collection("groups").document(group_id)
    metadata = {
        'upload_time': datetime.now().isoformat(),
        'file_count': len(files)-failed_file_quant,
        'group_name': group_id,
    }
    group_doc_ref.set({'metadata': metadata})

    metadata['files'] = files_metadata
    group_doc_ref.set({'metadata': metadata}, merge=True)

    # Build group_obj from the processing results.
    group_obj = {}
    for group in results.values():  # Iterate over values instead of keys
        group_obj[group['sanitized_filename']] = group['parsed_content']
    group_obj['group_id'] = group_id
    group_obj['metadata'] = metadata
    # asyncio.create_task(send_log("✅ Data extracted Successfully", user_room=client_id))



    # print(group_obj)
    return group_obj

@router.post("/update-change-log")
async def update_change_log(request: Request, firebase_user: dict = Depends(get_firebase_user)):
    user_id = firebase_user.get('userId')
    if not user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User ID not found")
    # print("************nfhfhhf*******")
    try:
        payload = await request.json()
        change_log = payload.get('change_log')

        if change_log is None or not isinstance(change_log, list):
            print(change_log + """""nnfjfjfjfjf""")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or missing change_log")

        client = await get_firestore_client()

        for record in change_log:
            action = record.get('action')
            path = record.get('path')
            if not action or not path:
                continue

            # Handle line item updates separately.
            if LINE_ITEM_REGEX.match(f"users/{user_id}/{path}"):
                await handle_line_item_update(record, user_id)
                continue

            # Prepare document reference.
            full_path = f"users/{user_id}/{path}"
            doc_ref = client.document(full_path)
            doc_snapshot = await document_get(doc_ref)
            doc_exists = doc_snapshot.exists

            if action == 'EDIT':
                await handle_document_edit(doc_ref, doc_exists, record)
            elif action == 'DELETE':
                await handle_document_delete(doc_ref, doc_exists, record, user_id)
            elif action == 'ADD':
                await handle_document_add(doc_ref, record)
            elif action == 'GROUP-DELETE':
                await handle_group_delete(doc_ref, doc_exists)
            else:
                continue

        return JSONResponse(content={'message': 'Change log processed successfully.'}, status_code=200)

    except Exception as e:
        logger.exception("Error processing change log")
        return JSONResponse(content={'error': str(e)}, status_code=500)

@router.get("/get-user-data")
async def get_user_data(request: Request, firebase_user: dict = Depends(get_firebase_user)):
    user_id = firebase_user.get('userId')
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID not found")
    try:
        groups_ref = (await get_firestore_client()).collection("users").document(user_id).collection("groups")
        groups_list = []
        for group_doc in await asyncio.to_thread(lambda: list(groups_ref.stream())):
            group_obj = {}
            group_data = group_doc.to_dict() or {}
            group_obj["group_id"] = group_doc.id
            group_obj["metadata"] = group_data.get("metadata", {})
            files_ref = group_doc.reference.collection("files")
            for file_doc in await asyncio.to_thread(lambda: list(files_ref.stream())):
                file_name = file_doc.id
                file_data = file_doc.to_dict()
                group_obj[file_name] = file_data
            groups_list.append(group_obj)

        def sort_key(group):
            upload_time_str = group.get("metadata", {}).get("upload_time")
            if upload_time_str:
                try:
                    dt = datetime.fromisoformat(upload_time_str)
                    return abs((datetime.now() - dt).total_seconds())
                except Exception:
                    return float('inf')
            return float('inf')

        sorted_groups = sorted(groups_list, key=sort_key)
        return JSONResponse(content={'uploads': sorted_groups}, status_code=200)

    except Exception as e:
        logger.exception("Failed to fetch user data")
        return JSONResponse(content={'error': 'Failed to fetch user data.'}, status_code=500)

@router.delete("/delete-group/{group_id}")
async def delete_group(group_id: str, request: Request, firebase_user: dict = Depends(get_firebase_user)):
    user_id = firebase_user.get('userId')
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID not found")
    try:
        group_doc_ref = (await get_firestore_client()).collection("users").document(user_id)\
            .collection("groups").document(group_id)
        group_snapshot = await document_get(group_doc_ref)
        if not group_snapshot.exists:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f'No group found with group_id: {group_id}')
        await document_delete(group_doc_ref)
        return JSONResponse(content={'message': f'Group {group_id} deleted successfully.'}, status_code=200)
    except Exception as e:
        logger.exception(f"Error deleting group {group_id}")
        return JSONResponse(content={'error': 'Failed to delete the group.'}, status_code=500)

@router.delete("/delete/groups/{group_id}/files/{file_name}/records/{record_key}")
async def delete_record(
    group_id: str, file_name: str, record_key: str, request: Request, firebase_user: dict = Depends(get_firebase_user)
):
    if not group_id or not file_name or not record_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing required parameters")
    user_id = firebase_user.get('userId')
    if not user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User ID not found")
    sanitized_file_name = file_name
    file_doc_ref = (await get_firestore_client()).collection("users").document(user_id)\
        .collection("groups").document(group_id).collection("files").document(sanitized_file_name)
    file_snapshot = await document_get(file_doc_ref)
    if not file_snapshot.exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")
    file_data = file_snapshot.to_dict()
    if record_key not in file_data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found in file")
    await document_update(file_doc_ref, {record_key: admin_fs.DELETE_FIELD})
    updated_data = (await document_get(file_doc_ref)).to_dict() or {}
    if len(updated_data) == 0:
        await document_delete(file_doc_ref)
    files_ref = (await get_firestore_client()).collection("users").document(user_id)\
        .collection("groups").document(group_id).collection("files")
    remaining_files = list(await stream_collection(files_ref))
    if len(remaining_files) == 0:
        group_doc_ref = (await get_firestore_client()).collection("users").document(user_id)\
            .collection("groups").document(group_id)
        await document_delete(group_doc_ref)
        return JSONResponse(content={'message': f'Record {record_key} deleted. Group {group_id} also deleted.'}, status_code=200)
    return JSONResponse(content={'message': f'Record {record_key} successfully deleted from {file_name}'}, status_code=200)

@router.put("/update/groups/{group_id}/files/{file_name}/records/{record_key}")
async def update_record(
    group_id: str, file_name: str, record_key: str, request: Request, firebase_user: dict = Depends(get_firebase_user)
):
    user_id = firebase_user.get('userId')
    if not user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User ID not found")
    try:
        updated_data = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing request body")
    sanitized_file_name = file_name
    file_doc_ref = (await get_firestore_client()).collection("users").document(user_id)\
        .collection("groups").document(group_id).collection("files").document(sanitized_file_name)
    if not (await document_get(file_doc_ref)).exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")
    await document_update(file_doc_ref, updated_data)
    return JSONResponse(content={'message': f'Record {record_key} successfully updated.'}, status_code=200)
from fastapi import APIRouter, Request, Depends, HTTPException, status
from fastapi.responses import JSONResponse

# These functions are assumed to be defined elsewhere in your codebase.
# - get_firebase_user: Dependency that returns the firebase user dict.
# - get_firestore_client: Returns a Firestore client instance.
# - document_get: Retrieves a document snapshot from Firestore.
# - document_update: Updates an existing document.


@router.put("/update-group-name/{group_id}")
async def update_group_name(
    group_id: str,
    request: Request,
    firebase_user: dict = Depends(get_firebase_user)
):
    # Get the user ID from the firebase user payload.
    user_id = firebase_user.get("userId")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User ID not found"
        )

    # Read the request payload and extract the new group name.
    try:
        payload = await request.json()
        new_group_name = payload.get("group_name")
        if not new_group_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing group_name in request body"
            )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request payload"
        )

    client = await get_firestore_client()

    # Reference to the group document within the user's groups subcollection.
    group_doc_ref = client.collection("users").document(user_id)\
                           .collection("groups").document(group_id)
    group_snapshot = await document_get(group_doc_ref)
    if not group_snapshot.exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No group found with group_id: {group_id}"
        )

    # Use dot notation to update only the metadata.group_name field.
    await document_update(group_doc_ref, {"metadata.group_name": new_group_name})

    return JSONResponse(
        content={
            "group_name": new_group_name,
            "message": f"Group name updated to {new_group_name} for group document {group_id}."
        },
        status_code=200
    )



@router.delete("/delete-all-data/{email}")
async def delete_all_data(
        email: str,
        request: Request,
        firebase_user: dict = Depends(get_firebase_user)
):
    # Validate authenticated user details
    user_id = firebase_user.get('userId')
    if not user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User ID not found")

    session_email = firebase_user.get("email")
    if not session_email:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email not found")

    if session_email.lower() != email.lower():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized: Email mismatch")

    firestore_client = await get_firestore_client()

    # Delete integrations collection data
    integrations_ref = firestore_client.collection("users").document(user_id).collection("integrations")
    integrations_snapshot = await document_get(integrations_ref)
    if integrations_snapshot and len(integrations_snapshot) > 0:
        try:
            for doc in integrations_snapshot:
                await document_delete(doc.reference)
        except Exception as e:
            raise HTTPException(status_code=500, detail="Failed to delete integrations data") from e

    # Delete groups collection data
    groups_ref = firestore_client.collection("users").document(user_id).collection("groups")
    groups_snapshot = await document_get(groups_ref)
    if groups_snapshot and len(groups_snapshot) > 0:
        try:
            for doc in groups_snapshot:
                await document_delete(doc.reference)
        except Exception as e:
            raise HTTPException(status_code=500, detail="Failed to delete groups data") from e

    return JSONResponse(content={'message': 'All your data has been deleted successfully'}, status_code=200)


