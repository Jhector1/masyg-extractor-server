import json
import os
from typing import Dict
import requests
from fastapi import APIRouter, Request, HTTPException, status, Depends
from fastapi.responses import JSONResponse
from firebase_admin import firestore

from masyg_extractor.config.jwt_config import get_current_user_from_cookie
from masyg_extractor.integration_qb_v5.core.integration_context import IntegrationContext
from masyg_extractor.integration_qb_v5.entity_helper import EntityHelper
from masyg_extractor.integration_qb_v5.intergrate.quickbooks.services.account_service import AccountService

from masyg_extractor.integration_qb_v5.intergrate.quickbooks.services.invoice_service import InvoiceService
from masyg_extractor.integration_qb_v5.repository.firestore_repository import QuickBooksFirestoreService
from masyg_extractor.integration_qb_v5.routers.route_helper import normalize_payload, handle_quickbooks_request
from masyg_extractor.integration_qb_v5.intergrate.quickbooks.adapter import QuickBooksClientAdapter
from masyg_extractor.integration_qb_v5.intergrate.quickbooks.services.document_service import DocumentService
from masyg_extractor.integrations.quickbooks.repository.firestore_repository import get_quickbooks_token
from masyg_extractor.integrations.quickbooks.services.quickbook_service import get_entities

from masyg_extractor.integrations.xero.xero_client import xero_request
from masyg_extractor.services.log_manager import LogManager
from masyg_extractor.services.my_log import send_log, logger
from masyg_extractor.integrations.quickbooks.authentication.quickbook_auth import router as auth_router
from masyg_extractor.services.progress_log import IntegrationsProgressLog, get_integrations_progress_logger_factory

router = APIRouter(prefix="/integrations/quickbooks")
router.include_router(auth_router, prefix="", tags=["Quickbooks Auth"])

#
# def normalize_payload(data, record_key: str) -> list:
#     """
#     Normalize the payload into a list of record dictionaries.
#     If data is a dict, iterate over its keys to form individual record entries.
#     """
#     records = []
#     if isinstance(data, dict):
#         for txn_id, record_obj in data.items():
#
#             group_id = record_obj.get("group_id")
#
#
#             if not group_id:
#                 records.append({
#                     "error": "Group ID is required.",
#                     record_key: record_obj,
#                     "transaction_id": txn_id
#                 })
#                 continue
#             for key, details in record_obj.items():
#                 if key == "group_id":
#                     continue
#                 txn_id_full = f"{key.strip()}-{txn_id.strip()}"
#
#
#                 customer = Customer(
#                     id=details.get("customer_id"),
#                     name=details.get("customer_name")
#                 )
#                 items = []
#                 for line_item in details.get("line_items", []):
#                     item = Item(
#                         id=line_item.get("item_id"),
#                         name=line_item.get("item_name"),
#                         quantity=line_item.get("quantity"),
#                         unit_price=line_item.get("unit_price"),
#                         description=line_item.get("description"),
#                         income_account=Account(
#                             id=line_item.get("income_account_id"),
#                             name=line_item.get("income_account"),
#                         ),
#                         expense_account=Account(
#                             id=line_item.get("expense_account_id"),
#                             name=line_item.get("expense_account"),
#                         ),
#                         sku=line_item.get("sku"),
#                         QtyOnHand=line_item.get("QtyOnHand", 1),
#                         type=line_item.get("type"),
#                         tax_code=line_item.get("tax"),
#                     )
#                     items.append(item)
#                 invoice = Invoice(
#                     customer=customer,
#                     items=items,
#                     date=details.get("date"),
#                     transaction_id=txn_id_full,
#                     group_id=group_id,
#
#                 )
#                 records.append(invoice)
#
#     else:
#         raise ValueError("Expected an object of records")
#
#     return records
#

# async def process_single_record(
#     item: dict,
#     send_func,
#     record_key: str,
#     client_id: str,
#     user_id: str,
#     request: Request,
#     idx: int,
#     total: int
#         ,progress_logger: IntegrationsProgressLog, progress: Dict[str, float],            share_progress: float,
#
# ) -> dict:
#     """
#     Process a single record:
#       - Send a progress update
#       - Validate required fields and date format
#       - Invoke the provided send function (invoice or receipt)
#       - Log outcomes and return the response or error
#     """
#
#     # await safe_emit("progress_update", {"progress": progress}, room=client_id)
#     # await asyncio.sleep(0.0)
#
#     customer_id = item.get("customer_id", "").strip()
#     customer_name = item.get("customer_name", "").strip()
#     date_str = format_date(item.get("date", "").strip())
#     group_id = item.get("group_id", "").strip()
#     line_items = item.get("line_items")
#     transaction_id = item.get("transaction_id", "").strip()
#
#     if not customer_name:
#         return {"error": "Customer name is required.", record_key: item}
#
#     if not date_str:
#         return {"error": "Date is required.", record_key: item}
#
#     try:
#         datetime.fromisoformat(date_str)
#     except ValueError:
#         return {"error": "Invalid date format. Expected YYYY-MM-DD.", record_key: item}
#
#     if not group_id:
#         return {"error": "Group ID is required.", record_key: item}
#
#     if not line_items or not isinstance(line_items, list):
#         return {"error": "A list of line_items is required.", record_key: item}
#
#     if not transaction_id:
#         return {"error": "Transaction ID is required.", record_key: item}
#
#     try:
#         response_data = await send_func(
#             request=request,
#             customer_name=customer_name,
#             customer_id=customer_id,
#             items=line_items,
#             transaction_id=transaction_id,
#             group_id=group_id,
#             date=date_str,
#             user_id=user_id,
#             progress_logger=progress_logger,
#             progress=progress,
#             share_progress=share_progress,
#             client_id=client_id
#         )
#         if "error" not in response_data:
#             asyncio.create_task(
#                 send_log(
#                     f"✅ {record_key.capitalize()} sent and processed {get_original_filename(transaction_id)} for {customer_name} successfully",
#                     user_room=client_id
#                 )
#             )
#         return response_data
#     except Exception as e:
#         error_msg = f"❌ Failed to process {record_key} for {get_original_filename(transaction_id)}"
#         asyncio.create_task(send_log(error_msg,log_key="qb-log-message", user_room=client_id))
#         return {
#             "error": f"Failed to send {record_key}",
#             "details": str(e),
#             record_key: item
#         }

@router.post("/send-salereceipt-in-bulk")
async def send_invoice_bulk_route(
    request: Request,
    global_progress: dict = Depends(IntegrationsProgressLog.get_file_progress_dict),
    progress_logger: IntegrationsProgressLog = Depends(get_integrations_progress_logger_factory("quickbooks-invoice-progress")),
    current_user: dict = Depends(get_current_user_from_cookie),
):
    """
    Handle sending invoices to QuickBooks.
    This endpoint:
      - Parses and validates the invoice payload.
      - Creates a QuickBooks integration context and client adapter.
      - Uses the InvoiceService to send invoices concurrently.
      - Emits progress updates to the client.
    """
    user_id: str = current_user["userId"]

    repo = QuickBooksFirestoreService(user_id=user_id, integration="quickbooks")
    log_manager_ = LogManager()
    data = await request.json()

    context = IntegrationContext(request=request,
                                 client_id=request.session["client_id"],
                                 progress_logger=progress_logger,
                                 progress=global_progress,
                                 doct_type="SalesReceipt",
                                 log_manager=log_manager_,
                                 user_id=user_id,
                                 )
    xero_client = QuickBooksClientAdapter(context)
    document_service = DocumentService(doc_type="SalesReceipt",
                                       doc_number_prefix="REC",
                                       context=context,
                                       repo=repo, client=xero_client)
    clean_data = await normalize_payload(data, "sales_receipt_data")

    # emit current overall (optional)
    await progress_logger.safe_emit_progress(global_progress)

    share_progress = IntegrationsProgressLog.CREATING_DOCUMENTS_WEIGHT
    return await document_service.send_document_in_bulk(clean_data, share_progress)


@router.post("/send-invoice-in-bulk")
async def send_invoice_bulk_route(
        request: Request,
        global_progress: dict = Depends(IntegrationsProgressLog.get_file_progress_dict),
        progress_logger: IntegrationsProgressLog = Depends(
            get_integrations_progress_logger_factory("quickbooks-invoice-progress")),
        current_user: dict = Depends(get_current_user_from_cookie),
):
    """
    Handle sending invoices to QuickBooks.
    This endpoint:
      - Parses and validates the invoice payload.
      - Creates a QuickBooks integration context and client adapter.
      - Uses the InvoiceService to send invoices concurrently.
      - Emits progress updates to the client.
    """
    user_id: str = current_user["userId"]

    repo = QuickBooksFirestoreService(user_id=user_id, integration="quickbooks")
    log_manager_ = LogManager()
    data = await request.json()

    context = IntegrationContext(request=request,
                                 client_id=request.session["client_id"],
                                 progress_logger=progress_logger,
                                 progress=global_progress,
                                 doct_type="Invoice",
                                 log_manager=log_manager_,
                                 user_id=user_id,
                                 )
    xero_client = QuickBooksClientAdapter(context)
    document_service = DocumentService(doc_type="Invoice",
                                       doc_number_prefix="Inv",
                                       context=context,
                                       repo=repo, client=xero_client)
    clean_data = await normalize_payload(data, "invoice_data")

    # emit current overall (optional)
    await progress_logger.safe_emit_progress(global_progress)

    share_progress = IntegrationsProgressLog.CREATING_DOCUMENTS_WEIGHT
    return await document_service.send_document_in_bulk(clean_data, share_progress)

    # user_id: str = current_user["userId"]
    #
    # repo = QuickBooksFirestoreService(user_id=user_id, integration="quickbooks")
    # log_manager_= LogManager()
    #
    #
    # context = IntegrationContext(request=request,
    #                              client_id=request.session["client_id"],
    #                              progress_logger=progress_logger,
    #                              progress=global_progress,
    #                              doct_type="Invoices",
    #                              log_manager=log_manager_,
    #                              user_id=user_id,
    #                              )
    # xero_client = QuickBooksClientAdapter(context)
    # account_service = AccountService(context=context,
    #                                  repo=repo,
    #                                  client=xero_client
    #                                  )
    # return await account_service.get_all_accounts()



@router.post("/send-invoice")
async def send_invoice_route(
    request: Request,
    global_progress: dict = Depends(IntegrationsProgressLog.get_file_progress_dict),
    progress_logger: IntegrationsProgressLog = Depends(get_integrations_progress_logger_factory("invoice-log-message")),
    current_user: dict = Depends(get_current_user_from_cookie),
):
    """
    Handle sending invoices to QuickBooks.
    This endpoint:
      - Parses and validates the invoice payload.
      - Creates a QuickBooks integration context and client adapter.
      - Uses the InvoiceService to send invoices concurrently.
      - Emits progress updates to the client.
    """
    return await handle_quickbooks_request(
        request=request,
        doc_type="Invoice",
        record_key="invoice_data",
        progress_event="quickbooks-invoice-progress",
        current_user=current_user,
        progress_logger=progress_logger,
        global_progress=global_progress,
        service_factory=InvoiceService,
        send_method_getter=lambda service: service.send_invoice,
    )


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
@router.get("/test-check-entity")
async def test_check_entity(
    entity: str,
    identifier_field: str,
    identifier_value: str,
    request: Request,
global_progress: Dict[str, float] = Depends(IntegrationsProgressLog.get_file_progress_dict),

                            progress_logger: IntegrationsProgressLog = Depends(get_integrations_progress_logger_factory("quickbooks-invoice-progress")),
                             current_user: dict = Depends(get_current_user_from_cookie)

):
    """
    Test endpoint to check whether an entity exists in QuickBooks.
    Query parameters:
      - entity: The type of entity to search (e.g., "Customer", "Item")
      - identifier_field: The field to filter by (e.g., "Name" or "DisplayName")
      - identifier_value: The value to search for
    """
    try:
        client_id = request.session.get("client_id") or 'Guest'

        user_id = current_user.get("userId")
        repo = QuickBooksFirestoreService(user_id=user_id)

        context = IntegrationContext(

            request=request,
            user_id=user_id,
            client_id=client_id,
            progress_logger=progress_logger,
            progress=global_progress
        )
        qb_client = QuickBooksClientAdapter(context)
        entity_helper = EntityHelper(context, repo, qb_client)

        exists = await entity_helper.check_entity_exists(entity, identifier_field, identifier_value)
        return {
            "entity": entity,
            "identifier_field": identifier_field,
            "identifier_value": identifier_value,
            "exists": exists
        }
    except Exception as e:
        logger.error(f"Error in /test-check-entity: {e}")
        raise HTTPException(status_code=500, detail=str(e))
