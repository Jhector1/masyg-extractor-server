import asyncio
import json
import os
from typing import Dict
import requests
from fastapi import APIRouter, Request, HTTPException, status, Depends
from fastapi.responses import JSONResponse
from firebase_admin import firestore

from masyg_extractor.config.jwt_config import get_current_user_from_cookie
from masyg_extractor.integrations.accounting.core.integration_context import IntegrationContext
from masyg_extractor.integrations.accounting.quickbooks.entity_helper import EntityHelper
from masyg_extractor.integrations.accounting.quickbooks.services.account_service import AccountService

from masyg_extractor.integrations.accounting.quickbooks.services.invoice_service import InvoiceService
from masyg_extractor.integrations.accounting.shared.firestore_repository import QuickBooksFirestoreService
from masyg_extractor.integrations.accounting.quickbooks.route_helper import normalize_payload, handle_quickbooks_request
from masyg_extractor.integrations.accounting.quickbooks.adapter import QuickBooksClientAdapter
from masyg_extractor.integrations.accounting.quickbooks.services.document_service import DocumentService
from masyg_extractor.integrations.accounting.shared.token_repository import get_integration_token as get_quickbooks_token
from masyg_extractor.integrations.accounting.quickbooks.services.quickbook_service import get_entities

from masyg_extractor.services.log_manager import LogManager
from masyg_extractor.services.my_log import send_log, logger
from masyg_extractor.integrations.accounting.quickbooks.authentication.quickbook_auth import router as auth_router
from masyg_extractor.services.progress_log import IntegrationsProgressLog, get_integrations_progress_logger_factory

router = APIRouter(prefix="/integrations/quickbooks")
router.include_router(auth_router, prefix="", tags=["Quickbooks Auth"])


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
    try:
        user_Id = current_user.get("userId")
        response = await get_entities(request, "Customer", user_Id)

        # Accept both JSONResponse and list
        if hasattr(response, "body"):
            raw = response.body
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8", errors="replace")
            customer_entities = json.loads(raw or "[]")
        else:
            customer_entities = response  # already a list/dict

        filtered = [
            {"Name": c.get("DisplayName"), "Id": c.get("Id")}
            for c in (customer_entities or [])
            if isinstance(c, dict)
        ]
        return JSONResponse(content=filtered, status_code=status.HTTP_200_OK)

    except Exception as e:
        logger.exception("Error processing customers")  # full stack trace
        raise HTTPException(status_code=500, detail="Failed to fetch customers")


@router.get("/get-vendors")
async def get_vendors(request: Request, current_user: dict = Depends(get_current_user_from_cookie)):
    try:
        user_Id = current_user.get("userId")
        response = await get_entities(request, "Vendor", user_Id)

        if hasattr(response, "body"):
            raw = response.body
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8", errors="replace")
            vendor_entities = json.loads(raw or "[]")
        else:
            vendor_entities = response

        filtered = [
            {"Name": v.get("DisplayName"), "Id": v.get("Id")}
            for v in (vendor_entities or [])
            if isinstance(v, dict)
        ]
        return JSONResponse(content=filtered, status_code=status.HTTP_200_OK)

    except Exception:
        logger.exception("Error processing vendors")
        raise HTTPException(status_code=500, detail="Failed to fetch vendors")


@router.get("/get-accounts")
async def get_accounts(
    request: Request,
    current_user: dict = Depends(get_current_user_from_cookie),
    account_types: str | None = None
):
    # masyg_extractor/integrations/quickbooks/quickbooks_client.py
    import os
    QUICKBOOKS_BASE = os.getenv("QUICKBOOKS_URL")

    user_id = current_user.get("userId")
    qb_data = get_quickbooks_token(user_id, "quickbooks")
    if not qb_data or "accessToken" not in qb_data or "realmId" not in qb_data:
        raise HTTPException(status_code=401, detail="User not authenticated")

    realm_id = qb_data["realmId"]
    access_token = qb_data["accessToken"]
    url = f"{QUICKBOOKS_BASE}/{realm_id}/query"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    if account_types:
        types_list = [t.strip() for t in account_types.split(",") if t.strip()]
        quoted = ", ".join([f"'{t}'" for t in types_list])
        query = f"SELECT * FROM Account WHERE AccountType IN ({quoted})"
    else:
        query = "SELECT * FROM Account"

    try:
        # requests is synchronous; keep the provider call off FastAPI's event loop.
        resp = await asyncio.to_thread(
            requests.get,
            url,
            headers=headers,
            params={"query": query},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        accounts = (data.get("QueryResponse", {}) or {}).get("Account", []) or []
        filtered = [{"Name": a.get("Name"), "Id": a.get("Id")} for a in accounts if isinstance(a, dict)]
        return JSONResponse(content=filtered, status_code=200)

    except requests.exceptions.HTTPError as exc:
        status_code = getattr(exc.response, "status_code", None) or getattr(resp, "status_code", 502)
        logger.warning("QuickBooks accounts query failed status=%s", status_code)
        # Do not reflect QuickBooks response bodies/tokens/provider details to clients.
        raise HTTPException(status_code=status_code, detail="QuickBooks query failed") from exc

    except requests.exceptions.RequestException as exc:
        logger.warning("QuickBooks accounts request failed error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="QuickBooks service unavailable") from exc
    except Exception as exc:
        logger.error("Unexpected QuickBooks accounts failure error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to fetch QuickBooks accounts") from exc

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
    except Exception as exc:
        logger.warning("Invalid QuickBooks config JSON error_type=%s", type(exc).__name__)
        return JSONResponse(
            {"error": "Invalid JSON payload"},
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
        await asyncio.to_thread(lambda: doc_ref.set({"config": config}, merge=True))
        return JSONResponse({"message": "Settings saved successfully"}, status_code=200)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("QuickBooks config save failed error_type=%s", type(exc).__name__)
        return JSONResponse(
            {"error": "Failed to save settings"},
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
    if os.getenv("FAST_API_ENV", "development").lower() == "production":
        raise HTTPException(status_code=404, detail="Not found")
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
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("QuickBooks entity check failed error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="QuickBooks entity check failed") from exc
