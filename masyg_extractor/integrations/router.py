from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from masyg_extractor.integrations.services.quickbook_service import get_entities
from masyg_extractor.services.my_log import send_log, logger
from masyg_extractor.integrations.services.invoice_service import InvoiceService

from masyg_extractor.integrations.authentication.quickbook_auth import router as auth_router
import asyncio
router = APIRouter(prefix="/integrations/quickbook")
router.include_router(auth_router, prefix="", tags=["QuickBooks Auth"])

@router.post("/send-invoice")
async def send_invoice_route(request: Request):
    """
    Handle sending invoices to QuickBooks.
    Normalizes the payload and processes each invoice individually.
    """
    client_id = request.cookies.get("clientId", "Guest")
    print(f"*************{client_id}**********************")
    firebase_user = request.session.get("user")
    if not firebase_user or not firebase_user.get("userId"):
        asyncio.create_task(send_log("User not authenticated", user_room=client_id))
        return JSONResponse({"error": "User not authenticated", "uploads": []}, status_code=401)

    user_id = firebase_user.get("userId")
    try:
        data = await request.json()
        asyncio.create_task(
            send_log("⚙️ Processing invoices...", user_room=client_id))

        # If the payload is a dict, transform it into a list of invoice objects
        if isinstance(data, dict):
            invoices = []
            for txn_id, invoice_obj in data.items():
                group_id = invoice_obj.get("group_id")
                if not group_id:
                    invoices.append({
                        "error": "Group ID is required.",
                        "invoice_data": invoice_obj,
                        "transaction_id": txn_id
                    })
                    continue
                for key, details in invoice_obj.items():
                    if key == "group_id":
                        continue
                    txn_id_full = f"{key.strip()}-{txn_id.strip()}"
                    invoice_data = details.copy()
                    invoice_data["file_name"] = key.strip()
                    invoice_data["group_id"] = group_id.strip()
                    invoice_data["transaction_id"] = txn_id_full
                    invoices.append(invoice_data)
            data = invoices
        elif not isinstance(data, list):
            return JSONResponse({"error": "Expected a list or object of invoices"}, status_code=400)

        responses = []
        for item in data:
            customer_id = item.get("customer_id", "").strip()
            customer_name = item.get("customer_name", "").strip()
            date = item.get("date", "").strip()
            line_items = item.get("line_items")
            transaction_id = item.get("transaction_id", "").strip()
            group_id = item.get("group_id", "").strip()

            if not customer_name:
                responses.append({"error": "Customer name is required.", "invoice_data": item})
                continue

            if not date:
                asyncio.create_task(
                    send_log("❌ Date is required", user_room=client_id))
                responses.append({"error": "Date is required.", "invoice_data": item})
                continue

            if not group_id:
                responses.append({"error": "Group ID is required.", "invoice_data": item})
                continue

            if not line_items or not isinstance(line_items, list):
                asyncio.create_task(
                    send_log("❌ A list of line_items is required", user_room=client_id))
                responses.append({"error": "A list of line_items is required.", "invoice_data": item})
                continue

            if not transaction_id:
                responses.append({"error": "Transaction ID is required.", "invoice_data": item})
                continue

            try:
                response_data = InvoiceService.send_invoice(
                    request=request,
                    customer_name=customer_name,
                    customer_id=customer_id,
                    items=line_items,
                    transaction_id=transaction_id,
                    group_id=group_id,
                    date=date,
                    user_id=user_id,
                    client_id=client_id
                )
                print(response_data)
                if not 'error' in response_data:
                    asyncio.create_task(
                    send_log(f"✅ Invoice sent and Processed {transaction_id} for {customer_name} successfully", user_room=client_id))
                responses.append(response_data)
            except Exception as e:
                error_msg = f"❌ Failed to process invoice for {transaction_id}: {str(e)}"
                asyncio.create_task(
                    send_log(error_msg, user_room=client_id))
                responses.append({
                    "error": "Failed to send invoice",
                    "details": str(e),
                    "invoice_data": item
                })

        return JSONResponse(content=responses)
    except Exception as e:
        error_msg = f"Failed to process /send-invoice request: {str(e)}"
        # asyncio.create_task(
        #     send_log(error_msg, user_room=client_id))
        return JSONResponse({"error": "Failed to process request", "details": str(e)}, status_code=500)

@router.get("/get-items")
async def get_items(request: Request):
    """
    Retrieves items from QuickBooks.
    """
    access_token = request.session.get("access_token")
    realm_id = request.session.get("realm_id")

    if not access_token or not realm_id:
        return JSONResponse({"error": "User not authenticated"}, status_code=401)

    # Example usage of quickbooks_request directly:
    from masyg_extractor.integrations.quickbooks_client import quickbooks_request
    url_params = {"query": "SELECT * FROM Item"}
    try:
        response = quickbooks_request(request, "query", method="GET", params=url_params)
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
        response = quickbooks_request(request, "query", method="GET", params=query)
        return JSONResponse(response)
    except Exception as e:
        logger.error(f"Error retrieving accounts: {str(e)}")
        return JSONResponse({"error": "Exception while retrieving accounts", "details": str(e)}, status_code=500)

@router.get("/get-customers")
async def get_customers(request: Request):
    """
    Fetches all customers from QuickBooks.
    """

    return await get_entities(request,"Customer")

@router.get("/get-vendors")
async def get_vendors(request: Request):
    """
    Fetches all vendors from QuickBooks.
    """

    return await get_entities(request,"Vendor")
