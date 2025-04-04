from fastapi import APIRouter, Request, HTTPException, status
from fastapi.responses import JSONResponse, RedirectResponse
from datetime import datetime
import asyncio
import requests
import base64

from masyg_extractor.integrations.xero.services.invoice_service import InvoiceService
from masyg_extractor.integrations.xero.authentication.xero_auth import router as auth_router
from masyg_extractor.integrations.xero.xero_client import xero_request
from masyg_extractor.utils.tool import get_original_filename
from masyg_extractor.services.my_log import send_log, logger

router = APIRouter(prefix="/integrations/xero")
router.include_router(auth_router, prefix="", tags=["Xero Auth"])


async def normalize_payload(data, record_key: str) -> list:
    """
    Normalize the payload into a list of record dictionaries.
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
) -> dict:
    """
    Process a single record: validate required fields, invoke the send function,
    and log outcomes.
    """
    progress = (100 / total) * (idx + 1)
    await asyncio.sleep(0.0)

    customer_id = item.get("customer_id", "").strip()
    customer_name = item.get("customer_name", "").strip()
    date_str = item.get("date", "").strip()
    try:
        # Validate date format
        datetime.fromisoformat(date_str)
    except Exception:
        return {"error": "Invalid date format. Expected YYYY-MM-DD.", record_key: item}
    group_id = item.get("group_id", "").strip()
    line_items = item.get("line_items")
    transaction_id = item.get("transaction_id", "").strip()

    if not customer_name:
        return {"error": "Customer name is required.", record_key: item}
    if not date_str:

        return {"error": "Date is required.", record_key: item}
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
            client_id=client_id
        )
        if "error" not in response_data:
            asyncio.create_task(
                send_log(
                    f"✅ Invoice sent and processed {get_original_filename(transaction_id)} for {customer_name} successfully",
                    user_room=client_id
                )
            )
        return response_data
    except Exception as e:
        error_msg = f"❌ Failed to process invoice for {get_original_filename(transaction_id)}"
        asyncio.create_task(send_log(error_msg, user_room=client_id))
        return {
            "error": f"Failed to send invoice",
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
    Handle sending invoices to Xero. Normalizes the payload,
    validates required fields, and processes each invoice concurrently.
    """
    client_id = request.cookies.get("clientId", "Guest")
    logger.info(f"Client ID: {client_id}")
    firebase_user = request.session.get("user")
    if not firebase_user or not firebase_user.get("userId"):
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
        normalized_records = await normalize_payload(data,log_key="xero-log-message", record_key="invoice_data")
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
    return JSONResponse(content=responses)


@router.get("/get-items")
async def get_items(request: Request):
    """
    Retrieves items from Xero, returning only the Name and Id for each item.
    """
    xero_data = request.session.get("xero")
    if not xero_data or "access_token" not in xero_data or "tenant_id" not in xero_data:
        return JSONResponse({"error": "User not authenticated"}, status_code=401)
    try:
        response = await xero_request(request, "Items", method="GET")
        # Assuming the response contains an "Items" key with the list of items
        items = response.get("Items", [])
        filtered_items = [
            {"Name": item.get("Name"), "Id": item.get("ItemID")}
            for item in items
        ]
        return JSONResponse(filtered_items, status_code=200)
    except Exception as e:
        logger.error(f"Error retrieving items: {str(e)}")
        return JSONResponse(
            {"error": "Exception while retrieving items", "details": str(e)},
            status_code=500
        )


@router.get("/get-customers")
async def get_customers(request: Request):
    """
    Fetches all customers (Contacts marked as customers) from Xero.
    """
    xero_data = request.session.get("xero")
    if not xero_data or "access_token" not in xero_data or "tenant_id" not in xero_data:
        return JSONResponse({"error": "User not authenticated"}, status_code=401)
    params = {"where": 'IsCustomer=true'}
    try:
        response = await xero_request(request, "Contacts", method="GET", params=params)
        customers = response.get("Contacts", [])
        filtered_customers = [
            {"Name": customer.get("Name"), "Id": customer.get("ContactID")}
            for customer in customers
        ]
        return JSONResponse(filtered_customers, status_code=200)
    except Exception as e:
        logger.error(f"Error retrieving customers: {str(e)}")
        return JSONResponse({"error": "Exception while retrieving customers", "details": str(e)}, status_code=500)


@router.get("/get-supplier")
async def get_vendors(request: Request):
    """
    Fetches all vendors (Contacts marked as suppliers) from Xero.
    """
    xero_data = request.session.get("xero")
    if not xero_data or "access_token" not in xero_data or "tenant_id" not in xero_data:
        return JSONResponse({"error": "User not authenticated"}, status_code=401)
    params = {"where": 'IsSupplier=true'}
    try:
        response = await xero_request(request, "Contacts", method="GET", params=params)
        suppliers = response.get("Contacts", [])
        filtered_suppliers = [
            {"Name": customer.get("Name"), "Id": customer.get("ContactID")}
            for customer in suppliers
        ]
        return JSONResponse(filtered_suppliers, status_code=200)
    except Exception as e:
        logger.error(f"Error retrieving vendors: {str(e)}")
        return JSONResponse({"error": "Exception while retrieving vendors", "details": str(e)}, status_code=500)


@router.get("/get-accounts")
async def get_accounts(request: Request, account_types: str = None):
    """
    Fetches accounts from Xero.

    Optional Query Parameter:
    - account_types: A comma-separated list of account types to filter by.
      For example: "REVENUE,EXPENSE"
    If omitted, returns all accounts.
    """
    xero_data = request.session.get("xero")
    if not xero_data or "access_token" not in xero_data or "tenant_id" not in xero_data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not authenticated")

    params = {}
    if account_types:
        types_list = [t.strip() for t in account_types.split(",") if t.strip()]
        where_clause = " OR ".join([f'Type=="{t}"' for t in types_list])
        params["where"] = where_clause

    try:
        response = await xero_request(request, "Accounts", method="GET", params=params)
        accounts = response.get("Accounts", [])
        return JSONResponse(content=accounts, status_code=status.HTTP_200_OK)
    except Exception as err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(err)}"
        )


@router.get("/get-income-accounts")
async def get_income_accounts(request: Request):
    """
    Fetches only Income Accounts (typically REVENUE) from Xero.
    """
    return await get_accounts(request, account_types="REVENUE")


@router.get("/get-expense-accounts")
async def get_expense_accounts(request: Request):
    """
    Fetches only Expense Accounts from Xero.
    """
    return await get_accounts(request, account_types="EXPENSE")
