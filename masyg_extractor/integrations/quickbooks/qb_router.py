import json
import os
from datetime import datetime
import asyncio
from typing import Dict

import requests
import base64

from fastapi import APIRouter, Request, HTTPException, status, Depends
from fastapi.responses import JSONResponse
from firebase_admin import firestore

from masyg_extractor.config.jwt_config import get_current_user_from_cookie
from masyg_extractor.integrations.quickbooks.repository.firestore_repository import get_quickbooks_token
from masyg_extractor.integrations.quickbooks.services.quickbook_service import get_entities
from masyg_extractor.integrations.quickbooks.services.receipt_service import ReceiptService
from masyg_extractor.integrations.quickbooks.services.invoice_service import InvoiceService
from masyg_extractor.integrations.utils import format_date
from masyg_extractor.services.my_log import send_log, logger
from masyg_extractor.integrations.quickbooks.authentication.quickbook_auth import router as auth_router
from masyg_extractor.services.progress_log import IntegrationsProgressLog, get_integrations_progress_logger
from masyg_extractor.utils.tool import get_original_filename

router = APIRouter(prefix="/integrations/quickbooks")
router.include_router(auth_router, prefix="", tags=["QuickBooks Auth"])


async def normalize_payload(data, record_key: str) -> list:
    """
    Normalize the payload into a list of record dictionaries.
    If data is a dict, iterate over its keys to form individual record entries.
    """
    records = []
    if isinstance(data, dict):
        for txn_id, record_obj in data.items():
            group_id = record_obj.get("group_id")
            if not group_id:
                records.append({
                    "error": "Group ID is required.",
                    record_key: record_obj,
                    "transaction_id": txn_id
                })
                continue
            for key, details in record_obj.items():
                if key == "group_id":
                    continue
                txn_id_full = f"{key.strip()}-{txn_id.strip()}"
                record_data = details.copy()
                record_data["file_name"] = key.strip()
                record_data["group_id"] = group_id.strip()
                record_data["transaction_id"] = txn_id_full
                records.append(record_data)
    elif isinstance(data, list):
        records = data
    else:
        raise ValueError("Expected a list or object of records")

    return records


async def process_single_record(
    item: dict,
    send_func,
    record_key: str,
    client_id: str,
    user_id: str,
    request: Request,
    idx: int,
    total: int
        ,progress_logger: IntegrationsProgressLog, progress: Dict[str, float],            share_progress: float,

) -> dict:
    """
    Process a single record:
      - Send a progress update
      - Validate required fields and date format
      - Invoke the provided send function (invoice or receipt)
      - Log outcomes and return the response or error
    """

    # await safe_emit("progress_update", {"progress": progress}, room=client_id)
    # await asyncio.sleep(0.0)

    customer_id = item.get("customer_id", "").strip()
    customer_name = item.get("customer_name", "").strip()
    date_str = format_date(item.get("date", "").strip())
    group_id = item.get("group_id", "").strip()
    line_items = item.get("line_items")
    transaction_id = item.get("transaction_id", "").strip()

    if not customer_name:
        return {"error": "Customer name is required.", record_key: item}

    if not date_str:
        return {"error": "Date is required.", record_key: item}

    try:
        datetime.fromisoformat(date_str)
    except ValueError:
        return {"error": "Invalid date format. Expected YYYY-MM-DD.", record_key: item}

    if not group_id:
        return {"error": "Group ID is required.", record_key: item}

    if not line_items or not isinstance(line_items, list):
        return {"error": "A list of line_items is required.", record_key: item}

    if not transaction_id:
        return {"error": "Transaction ID is required.", record_key: item}

    try:
        response_data = await send_func(
            request=request,
            customer_name=customer_name,
            customer_id=customer_id,
            items=line_items,
            transaction_id=transaction_id,
            group_id=group_id,
            date=date_str,
            user_id=user_id,
            progress_logger=progress_logger,
            progress=progress,
            share_progress=share_progress,
            client_id=client_id
        )
        if "error" not in response_data:
            asyncio.create_task(
                send_log(
                    f"✅ {record_key.capitalize()} sent and processed {get_original_filename(transaction_id)} for {customer_name} successfully",
                    user_room=client_id
                )
            )
        return response_data
    except Exception as e:
        error_msg = f"❌ Failed to process {record_key} for {get_original_filename(transaction_id)}"
        asyncio.create_task(send_log(error_msg,log_key="qb-log-message", user_room=client_id))
        return {
            "error": f"Failed to send {record_key}",
            "details": str(e),
            record_key: item
        }


async def process_records(
    records: list,
    send_func,
    record_key: str,
    client_id: str,
    user_id: str,
    request: Request,
progress_logger: IntegrationsProgressLog, progress: Dict[str, float],
) -> list:
    """
    Process all records concurrently using asyncio.gather.
    """
    docs_stage_weight = IntegrationsProgressLog.CREATING_DOCUMENTS_WEIGHT  # GPT stage contributes 50% to overall progress

    total = len(records)
    chunk_share = docs_stage_weight / total
    tasks = [
        process_single_record(item, send_func, record_key, client_id, user_id, request, idx, total, progress_logger,progress, chunk_share)
        for idx, item in enumerate(records)
    ]
    responses = await asyncio.gather(*tasks, return_exceptions=True)
    progress["creating_documents"] = docs_stage_weight
    await progress_logger.safe_emit_progress(progress_logger.calculate_overall_progress(progress))

    return responses


@router.post("/send-invoice")
async def send_invoice_route(request: Request,global_progress: Dict[str, float] = Depends(IntegrationsProgressLog.get_file_progress_dict),
        progress_logger: IntegrationsProgressLog = Depends(get_integrations_progress_logger), current_user: dict = Depends(get_current_user_from_cookie)):
    """
    Handle sending invoices to QuickBooks.
    Normalizes the payload, validates required fields, and processes each invoice concurrently.
    """

    client_id = request.session.get("client_id") or 'Guest'
    # _last_emitted_overall.pop(client_id, None)
    # await send_log("⚙️ Processing invoices...", user_room=client_id)
    logger.info(f"Client ID: {client_id}")
    user_id = current_user.get("userId")
    if not user_id:
        return JSONResponse({"error": "User not authenticated", "uploads": []}, status_code=401)

    try:
        data = await request.json()
    except Exception as e:
        error_msg = f"Failed to parse JSON: {str(e)}"
        logger.error(error_msg)
        return JSONResponse({"error": error_msg}, status_code=400)

    asyncio.create_task(send_log("⚙️ Processing invoices...",log_key="qb-log-message", user_room=client_id))

    try:
        normalized_records = await normalize_payload(data, record_key="invoice_data")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    responses = await process_records(
        records=normalized_records,
        send_func=InvoiceService.send_invoice,
        record_key="invoice_data",
        client_id=client_id,
        user_id=user_id,
        request=request,
        progress_logger=progress_logger,
        progress=global_progress,

    )
    await send_log("⚙️ Processing invoices...", log_key="quickbooks-progress", user_room=client_id)

    return JSONResponse(content=responses)


@router.post("/send-receipt")
async def send_receipt_route(request: Request,current_user: dict = Depends(get_current_user_from_cookie)):
    """
    Handle sending sales receipts to QuickBooks.
    Normalizes the payload, validates required fields, and processes each receipt concurrently.
    """
    client_id = current_user.get("clientId", "Guest")
    logger.info(f"Client ID: {client_id}")
    user_id = request.session.get("user")
    if not user_id:
        asyncio.create_task(send_log("User not authenticated", user_room=client_id))
        return JSONResponse({"error": "User not authenticated", "uploads": []}, status_code=401)


    try:
        data = await request.json()
    except Exception as e:
        error_msg = f"Failed to parse JSON: {str(e)}"
        logger.error(error_msg)
        return JSONResponse({"error": error_msg}, status_code=400)

    asyncio.create_task(send_log("⚙️ Processing receipts...", user_room=client_id))

    try:
        normalized_records = await normalize_payload(data, record_key="receipt_data")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    responses = await process_records(
        records=normalized_records,
        send_func=ReceiptService.send_receipt,
        record_key="receipt_data",
        client_id=client_id,
        user_id=user_id,
        request=request
    )

    return JSONResponse(content=responses)

@router.get("/get-items", status_code=status.HTTP_200_OK)
async def get_items(request: Request, current_user: dict = Depends(get_current_user_from_cookie)):
    """
    Retrieves items from QuickBooks, returning only the Name and Id for each item.
    """
    user_Id = current_user.get("userId")
    # Call get_entities helper with "Item" as the entity type.
    response = await get_entities(request, "Item", user_Id)
    items_entities = json.loads(response.body)
    # Filter each item to return only the Name and Id.
    filtered_items = [{"Name": item.get("Name"), "Id": item.get("Id")} for item in items_entities]
    return JSONResponse(content=filtered_items, status_code=status.HTTP_200_OK)


@router.get("/get-customers")
async def get_customers(request: Request, current_user: dict = Depends(get_current_user_from_cookie)):
    """
    Fetches all customers from QuickBooks, returning only the DisplayName as Name and Id.
    """
    try:
        user_Id = current_user.get("userId")

        # get_entities returns a JSONResponse containing a list of customer objects.
        response = await get_entities(request, "Customer", user_Id)
        # Parse the JSONResponse body into a Python list.
        customer_entities = json.loads(response.body)
        # Filter out only the DisplayName as Name and Id.
        filtered_customers = [
            {"Name": customer.get("DisplayName"), "Id": customer.get("Id")}
            for customer in customer_entities
        ]
        return JSONResponse(content=filtered_customers, status_code=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"Error processing customers: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get-vendors")
async def get_vendors(request: Request, current_user: dict = Depends(get_current_user_from_cookie)):
    """
    Fetches all vendors from QuickBooks.
    """
    try:
        user_Id = current_user.get("userId")

        response = await get_entities(request, "Vendor", user_Id)
        customer_entities = json.loads(response.body)

        filtered_customers = [
            {"Name": customer.get("DisplayName"), "Id": customer.get("Id")}
            for customer in customer_entities
        ]

        return JSONResponse(content=filtered_customers, status_code=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"Error processing vendors: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get-accounts")
async def get_accounts(request: Request, current_user: dict = Depends(get_current_user_from_cookie),  account_types: str = None):
    """
    Fetches accounts from QuickBooks, returning only the Name and Id.

    Optional Query Parameter:
    - account_types: A comma-separated list of account types to filter by.
      For example: "Income,Cost of Goods Sold"
    If omitted, returns all accounts.
    """
    user_id = current_user.get("userId")

    qb_data = get_quickbooks_token(user_id, "quickbooks")

    if not qb_data or "accessToken" not in qb_data or "realmId" not in qb_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not authenticated"
        )
    realm_id = qb_data.get("realmId")
    access_token = qb_data.get("accessToken")

    url = f"{os.getenv("QUICKBOOKS_URL")}/{realm_id}/query"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }

    if account_types:
        types_list = [t.strip() for t in account_types.split(",") if t.strip()]
        quoted_types = ", ".join([f"'{t}'" for t in types_list])
        query = f"SELECT * FROM Account WHERE AccountType IN ({quoted_types})"
    else:
        query = "SELECT * FROM Account"

    params = {"query": query}

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        accounts = data.get("QueryResponse", {}).get("Account", [])
        filtered_accounts = [
            {"Name": account.get("Name"), "Id": account.get("Id")}
            for account in accounts
        ]
        return JSONResponse(content=filtered_accounts, status_code=status.HTTP_200_OK)
    except requests.exceptions.HTTPError:
        try:
            error_details = response.json()
        except Exception:
            error_details = {}
        raise HTTPException(
            status_code=response.status_code,
            detail={
                "error": "Failed to fetch accounts",
                "details": error_details
            }
        )
    except Exception as err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(err)}"
        )

@router.put("/save-config")
async def save_config(request: Request,current_user: dict = Depends(get_current_user_from_cookie)):
    """
    Save or update integration configuration settings in Firestore.
    Expects a JSON payload with:
    {
       "integration": "quickbooks" or "xero",
       "config": { ... }  // integration-specific config data
    }
    The config is saved under: users/{user_id}/integrations/{integration}
    """
    user_id = current_user.get("userId")
    if not user_id:
        return JSONResponse({"error": "User not authenticated"}, status_code=401)

    try:
        body = await request.json()
    except Exception as e:
        return JSONResponse(
            {"error": "Invalid JSON payload", "details": str(e)},
            status_code=400
        )

    integration = body.get("integration")
    config = body.get("config")
    if integration not in ["quickbooks", "xero"]:
        return JSONResponse(
            {"error": "Invalid integration type. Expected 'quickbooks' or 'xero'."},
            status_code=400
        )
    if not config:
        return JSONResponse(
            {"error": "Config object is required"},
            status_code=400
        )

    try:
        db = firestore.client()
        # Save the config under the user's integrations collection, updating the document if it already exists.

        doc_ref = db.collection("users").document(user_id)\
                    .collection("integrations").document('quickbooks')
        doc_ref.set({"config": config}, merge=True)
        return JSONResponse({"message": "Settings saved successfully"}, status_code=200)
    except Exception as e:
        return JSONResponse(
            {"error": "Failed to save settings", "details": str(e)},
            status_code=500
        )


@router.get("/get-income-accounts")
async def get_income_accounts(request: Request, current_user: dict = Depends(get_current_user_from_cookie),):
    """
    Fetches only Income Accounts from QuickBooks.
    """
    return await get_accounts(request, current_user, account_types="Income")


@router.get("/get-expense-accounts")
async def get_expense_accounts(request: Request, current_user: dict = Depends(get_current_user_from_cookie),):
    """
    Fetches only Expense Accounts (e.g., Cost of Goods Sold) from QuickBooks.
    """
    return await get_accounts(request, current_user, account_types="Cost of Goods Sold")
