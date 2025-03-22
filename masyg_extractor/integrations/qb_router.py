from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from datetime import datetime
import asyncio

from masyg_extractor.integrations.services.quickbook_service import get_entities
from masyg_extractor.integrations.services.receipt_service import ReceiptService
from masyg_extractor.integrations.services.invoice_service import InvoiceService
from masyg_extractor.integrations.utils import format_date
from masyg_extractor.services.my_log import send_log, logger
from masyg_extractor.integrations.authentication.quickbook_auth import router as auth_router
from masyg_extractor.utils.extensions import sio
from masyg_extractor.utils.tool import get_original_filename

router = APIRouter(prefix="/integrations/quickbook")
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
    print(records)
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
) -> dict:
    """
    Process a single record:
      - Send a progress update
      - Validate required fields and date format
      - Invoke the provided send function (invoice or receipt)
      - Log outcomes and return the response or error
    """
    progress = (100 / total) * (idx + 1)
    await sio.emit("progress_update", {"progress": progress}, room=client_id)
    await asyncio.sleep(.01)

    customer_id = item.get("customer_id", "").strip()
    customer_name = item.get("customer_name", "").strip()
    date_str = format_date(item.get("date", "").strip())
    group_id = item.get("group_id", "").strip()
    line_items = item.get("line_items")
    transaction_id = item.get("transaction_id", "").strip()

    if not customer_name:
        return { "error": "Customer name is required.", record_key: item }

    if not date_str:
        asyncio.create_task(send_log("❌ Date is required", user_room=client_id))
        return { "error": "Date is required.", record_key: item }

    try:
        datetime.fromisoformat(date_str)
    except ValueError:
        return { "error": "Invalid date format. Expected YYYY-MM-DD.", record_key: item }

    if not group_id:
        return { "error": "Group ID is required.", record_key: item }

    if not line_items or not isinstance(line_items, list):
        return { "error": "A list of line_items is required.", record_key: item }

    if not transaction_id:
        return { "error": "Transaction ID is required.", record_key: item }

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
        asyncio.create_task(send_log(error_msg, user_room=client_id))
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
    request: Request
) -> list:
    """
    Process all records concurrently using asyncio.gather.
    """
    total = len(records)
    tasks = [
        process_single_record(item, send_func, record_key, client_id, user_id, request, idx, total)
        for idx, item in enumerate(records)
    ]
    responses = await asyncio.gather(*tasks, return_exceptions=True)
    return responses


@router.post("/send-invoice")
async def send_invoice_route(request: Request):
    """
    Handle sending invoices to QuickBooks.
    Normalizes the payload, validates required fields, and processes each invoice concurrently.
    """
    client_id = request.cookies.get("clientId", "Guest")
    logger.info(f"Client ID: {client_id}")
    firebase_user = request.session.get("user")
    if not firebase_user or not firebase_user.get("userId"):
        asyncio.create_task(send_log("User not authenticated", user_room=client_id))
        return JSONResponse({"error": "User not authenticated", "uploads": []}, status_code=401)

    user_id = firebase_user.get("userId")
    try:
        data = await request.json()
    except Exception as e:
        error_msg = f"Failed to parse JSON: {str(e)}"
        logger.error(error_msg)
        return JSONResponse({"error": error_msg}, status_code=400)

    asyncio.create_task(send_log("⚙️ Processing invoices...", user_room=client_id))

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
        request=request
    )
    print(responses)
    return JSONResponse(content=responses)


@router.post("/send-receipt")
async def send_receipt_route(request: Request):
    """
    Handle sending sales receipts to QuickBooks.
    Normalizes the payload, validates required fields, and processes each receipt concurrently.
    """
    client_id = request.cookies.get("clientId", "Guest")
    logger.info(f"Client ID: {client_id}")
    firebase_user = request.session.get("user")
    if not firebase_user or not firebase_user.get("userId"):
        asyncio.create_task(send_log("User not authenticated", user_room=client_id))
        return JSONResponse({"error": "User not authenticated", "uploads": []}, status_code=401)

    user_id = firebase_user.get("userId")
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
    print(responses)
    return JSONResponse(content=responses)

@router.get("/get-items")
async def get_items(request: Request):
    """
    Retrieves items from QuickBooks.
    """
    access_token = request.session.get("access_token")
    realm_id = request.session.get("realm_id")

    if not access_token or not realm_id:
        return JSONResponse({"error": "User not authenticated"}, status_code=401)

    from masyg_extractor.integrations.quickbooks_client import quickbooks_request
    url_params = {"query": "SELECT * FROM Item"}
    try:
        response = await quickbooks_request(request, "query", method="GET", params=url_params)
        if "Fault" in response:
            error_info = response["Fault"].get("Error", [{}])[0]
            error_message = error_info.get("Message", "Unknown error")
            return JSONResponse({"error": error_message, "details": response}, status_code=401)
        return JSONResponse(response, status_code=200)
    except Exception as e:
        logger.error(f"Error retrieving items: {str(e)}")
        return JSONResponse({"error": "Exception while retrieving items", "details": str(e)}, status_code=500)

@router.get("/get-accounts")
async def get_accounts(request: Request):
    """
    Retrieves expense accounts from QuickBooks.
    """
    from masyg_extractor.integrations.quickbooks_client import quickbooks_request
    access_token = request.session.get("access_token")
    realm_id = request.session.get("realm_id")

    if not access_token:
        return JSONResponse({"error": "User not authenticated"}, status_code=401)

    query = {"query": "SELECT * FROM Account WHERE AccountType = 'Expense'"}
    try:
        response = await quickbooks_request(request, "query", method="GET", params=query)
        return JSONResponse(response)
    except Exception as e:
        logger.error(f"Error retrieving accounts: {str(e)}")
        return JSONResponse({"error": "Exception while retrieving accounts", "details": str(e)}, status_code=500)

@router.get("/get-customers")
async def get_customers(request: Request):
    """
    Fetches all customers from QuickBooks.
    """
    return await get_entities(request, "Customer")

@router.get("/get-vendors")
async def get_vendors(request: Request):
    """
    Fetches all vendors from QuickBooks.
    """
    return await get_entities(request, "Vendor")
