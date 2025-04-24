
from fastapi import APIRouter, Request, HTTPException, status, Depends
from fastapi.responses import JSONResponse

from masyg_extractor.config.jwt_config import get_current_user_from_cookie
from masyg_extractor.integration_v4.core.integration_context import IntegrationContext
from masyg_extractor.integration_v4.intergrate.quickbooks.services.account_service import AccountService

from masyg_extractor.integration_v4.intergrate.quickbooks.services.invoice_service import InvoiceService
from masyg_extractor.integration_v4.repository.firestore_repository import QuickBooksFirestoreService
from masyg_extractor.integration_v4.routers.route_helper import normalize_payload, handle_quickbooks_request
from masyg_extractor.integration_v4.intergrate.quickbooks.adapter import XeroClientAdapter
from masyg_extractor.integration_v4.intergrate.quickbooks.services.document_service import DocumentService

from masyg_extractor.integrations.xero.xero_client import xero_request
from masyg_extractor.services.log_manager import LogManager
from masyg_extractor.services.my_log import send_log, logger
from masyg_extractor.integrations.xero.authentication.xero_auth import router as auth_router
from masyg_extractor.services.progress_log import IntegrationsProgressLog, get_integrations_progress_logger_factory, \
    XeroIntegrationsProgressLog

router = APIRouter(prefix="/integrations/xero")
router.include_router(auth_router, prefix="", tags=["Xero Auth"])

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
@router.post("/send-invoice-in-bulk")
async def send_invoice_bulk_route(
    request: Request,
    global_progress: dict = Depends(XeroIntegrationsProgressLog.get_file_progress_dict),
    progress_logger: XeroIntegrationsProgressLog = Depends(get_integrations_progress_logger_factory("xero-invoice-progress")),
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

    repo = QuickBooksFirestoreService(user_id=user_id, integration="xero")
    log_manager_= LogManager()
    data = await request.json()

    context = IntegrationContext(request=request,
                                 client_id=request.session["client_id"],
                                 progress_logger=progress_logger,
                                 progress=global_progress,
                                 doct_type="Invoices",
                                 log_manager=log_manager_,
                                 user_id=user_id,
                                 )
    xero_client = XeroClientAdapter(context)
    document_service = DocumentService(doc_type="Invoices",
                                       doc_number_prefix="Inv",
                                       context=context,
                                       repo=repo,client=xero_client)
    clean_data = await normalize_payload(data, "invoice_data")

    return await document_service.send_document_in_bulk(clean_data,     await progress_logger.safe_emit_progress(progress_logger.calculate_overall_progress(global_progress))
 )



@router.get("/income-accounts")
async def get_income_accounts(
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
    user_id: str = current_user["userId"]

    repo = QuickBooksFirestoreService(user_id=user_id, integration="xero")
    log_manager_= LogManager()


    context = IntegrationContext(request=request,
                                 client_id=request.session["client_id"],
                                 progress_logger=progress_logger,
                                 progress=global_progress,
                                 doct_type="Invoices",
                                 log_manager=log_manager_,
                                 user_id=user_id,
                                 )
    xero_client = XeroClientAdapter(context)
    account_service = AccountService(context=context,
                                     repo=repo,
                                     client=xero_client
                                     )
    return await account_service.get_all_accounts()



@router.post("/send-invoice")
async def send_invoice_route(
    request: Request,
    global_progress: dict = Depends(IntegrationsProgressLog.get_file_progress_dict),
    progress_logger: IntegrationsProgressLog = Depends(get_integrations_progress_logger_factory("xero-invoice-log-message")),
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



async def fetch_xero_items(request: Request, user_id: str) -> list:
    response = await xero_request("Items", user_id=user_id, method="GET")
    items = response.get("Items", [])
    return [
        {"Name": item.get("Name"), "Id": item.get("ItemID")}
        for item in items
    ]



@router.get("/get-items")
async def get_items(request: Request, current_user: dict = Depends(get_current_user_from_cookie)):
    """
    Retrieves items from Xero, returning only the Name and Id for each item.
    """
    # xero_data = request.session.get("xero")
    # if not xero_data or "access_token" not in xero_data or "tenant_id" not in xero_data:
    #     return JSONResponse({"error": "User not authenticated"}, status_code=401)
    user_Id = current_user.get("userId")

    try:
        response = await xero_request( "Items", user_id=user_Id,method="GET")
        # Assuming the response contains an "Items" key with the list of items
        items = response.get("Items", [])
        filtered_items = [
            {"Name": item.get("Name"), "Id": item.get("Code")}
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
async def get_customers(request: Request, current_user: dict = Depends(get_current_user_from_cookie)):
    """
    Fetches all customers (Contacts marked as customers) from Xero.
    """
    # xero_data = request.session.get("xero")
    # if not xero_data or "access_token" not in xero_data or "tenant_id" not in xero_data:
    #     return JSONResponse({"error": "User not authenticated"}, status_code=401)
    user_Id = current_user.get("userId")

    params = {"where": 'IsCustomer=true'}
    try:
        response = await xero_request("Contacts", user_id=user_Id,method="GET", params=params)
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
async def get_vendors(request: Request, current_user: dict = Depends(get_current_user_from_cookie)):
    """
    Fetches all vendors (Contacts marked as suppliers) from Xero.
    """
    # xero_data = request.session.get("xero")
    # if not xero_data or "access_token" not in xero_data or "tenant_id" not in xero_data:
    #     return JSONResponse({"error": "User not authenticated"}, status_code=401)
    user_Id = current_user.get("userId")

    params = {"where": 'IsSupplier=true'}
    try:
        response = await xero_request(  "Contacts",user_Id, method="GET", params=params)
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
async def get_accounts(request: Request,current_user: dict = Depends(get_current_user_from_cookie), account_types: str = None):
    """
    Fetches accounts from Xero.

    Optional Query Parameter:
    - account_types: A comma-separated list of account types to filter by.
      For example: "REVENUE,EXPENSE"
    If omitted, returns all accounts.
    """
    # xero_data = request.session.get("xero")
    # if not xero_data or "access_token" not in xero_data or "tenant_id" not in xero_data:
    #     raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not authenticated")
    user_Id = current_user.get("userId")
    params = {}
    if account_types:
        types_list = [t.strip() for t in account_types.split(",") if t.strip()]
        where_clause = " OR ".join([f'Type=="{t}"' for t in types_list])
        params["where"] = where_clause

    try:
        response = await xero_request( "Accounts", user_id=user_Id, method="GET", params=params)
        accounts = response.get("Accounts", [])
        return JSONResponse(content=accounts, status_code=status.HTTP_200_OK)
    except Exception as err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(err)}"
        )


@router.get("/get-income-accounts")
async def get_income_accounts(request: Request, current_user: dict = Depends(get_current_user_from_cookie)):
    """
    Fetches only Income Accounts (typically REVENUE) from Xero.
    """
    return await get_accounts(request, current_user, account_types="REVENUE")


@router.get("/get-expense-accounts")
async def get_expense_accounts(request: Request, current_user: dict = Depends(get_current_user_from_cookie)):
    """
    Fetches only Expense Accounts from Xero.
    """
    return await get_accounts(request,current_user, account_types="EXPENSE")
