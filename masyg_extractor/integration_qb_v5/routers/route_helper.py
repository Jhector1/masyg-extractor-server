import asyncio
from typing import Any, Dict, List, Union
from fastapi import APIRouter, Request, HTTPException, status, Depends

from masyg_extractor.global_helper import transform_value
from masyg_extractor.integration_qb_v5.core.integration_context import IntegrationContext
from masyg_extractor.integration_qb_v5.domain.models import *
from masyg_extractor.integration_qb_v5.intergrate.quickbooks.adapter import QuickBooksClientAdapter
from masyg_extractor.integration_qb_v5.repository.firestore_repository import QuickBooksFirestoreService
from masyg_extractor.integration_qb_v5.utils import parse_int, parse_float
from masyg_extractor.integrations.utils import format_date
from masyg_extractor.integrations.xero.services.item_services import generate_sku
from masyg_extractor.services.file_extractor_service import remove_non_alphanumeric
from masyg_extractor.services.log_manager import LogManager
from masyg_extractor.services.my_log import logger
from masyg_extractor.services.progress_log import IntegrationsProgressLog
from fastapi.responses import JSONResponse

from masyg_extractor.utils.extensions import sio


# Helper function to create a Customer object
def create_customer(details: Dict[str, Any], transaction_id) -> Customer:
    """
    Create a Customer object from the details dictionary.
    """
    return Customer(
        id=details.get("customer_id"),
        name=remove_non_alphanumeric(details.get("customer_name")),
        transaction_id=transaction_id
    )


# Helper function to create an Item object
def create_item(line_item: Dict[str, Any], transaction_id) -> Item:
    """
    Create an Item object from a line item dictionary.
    """
    cleaned_name = remove_non_alphanumeric(line_item.get("item_name", ""))
    name = cleaned_name[:100]
    return Item(
        id=line_item.get("item_id"),
        name=name,
        quantity=parse_int(line_item.get("quantity")),
        unit_price=parse_float(line_item.get("unit_price")),
        description=line_item.get("description"),
        income_account=Account(
            id=line_item.get("income_account_id"),
            name=line_item.get("income_account"),
            transaction_id=transaction_id
        ),
        expense_account=Account(
            id=line_item.get("expense_account_id"),
            name=line_item.get("expense_account"),
            transaction_id=transaction_id
        ),
        transaction_id=transaction_id,

        sku=generate_sku(name),
        QtyOnHand=line_item.get("QtyOnHand", 1),
        type=line_item.get("type"),
        tax_code=transform_value(line_item.get("tax"), "NON"),
    )

import datetime


# Get the current date
current_date = datetime.date.today()

# Format the current date as "YYYY-MM-DD"
formatted_date = current_date.strftime("%Y-%m-%d")
# Helper function to create an Invoice object
def create_document(txn_id_full: str, group_id: str, details: Dict[str, Any]) -> Document:
    """
    Create an Invoice object using details from the record.
    """
    customer = create_customer(details, txn_id_full)
    items = [create_item(item, txn_id_full) for item in details.get("line_items", [])]
    return Document(
        customer=customer,
        items=items,
        date=format_date(transform_value(details.get("date"), formatted_date)),
        due_date=format_date(transform_value(details.get("due_date"), formatted_date)),
        transaction_id=txn_id_full,
        group_id=group_id,
    )


# Main function to normalize the payload
async def normalize_payload(data: Any, record_key: str) -> List[Union[Document, Dict[str, Any]]]:
    """
    Normalize the payload into a list of record dictionaries.
    If data is a dict, iterate over its keys to form individual record entries.
    """
    records: List[Union[Document, Dict[str, Any]]] = []

    if not isinstance(data, dict):
        raise ValueError("Expected an object of records")

    for txn_id, record_obj in data.items():
        group_id = record_obj.get("group_id")
        if not group_id:
            records.append({
                "error": "Group ID is required.",
                record_key: record_obj,
                "transaction_id": txn_id
            })
            continue

        # Process all keys except 'group_id'
        for key, details in record_obj.items():
            if key == "group_id":
                continue
            txn_id_full = f"{key.strip()}-{txn_id.strip()}"
            document = create_document(txn_id_full, group_id, details)
            records.append(document)

    return records





async def process_records(
    records: list,
    send_func,
    progress_logger: IntegrationsProgressLog,
    progress: dict,
) -> list:
    """
    Process all records concurrently using asyncio.gather.
    The progress update calculates a share for each record based on a predefined
    documents creation weight.
    """
    docs_stage_weight = IntegrationsProgressLog.CREATING_DOCUMENTS_WEIGHT  # e.g., 50% to overall progress
    total = len(records)
    chunk_share = docs_stage_weight / total if total else 0
    tasks = [send_func(document, chunk_share) for document in records]
    responses = await asyncio.gather(*tasks, return_exceptions=True)
    progress["creating_invoice"] = docs_stage_weight
    overall_progress = progress_logger.calculate_overall_progress(progress)
    await progress_logger.safe_emit_progress(overall_progress)
    return responses


def create_integration_context(
    request: Request,
    user_id: str,
    client_id: str,
    progress_logger: IntegrationsProgressLog,
    global_progress: dict,
    doc_type: str,
        log_manager: LogManager,
) -> IntegrationContext:
    """
    Creates and returns a shared IntegrationContext.
    """
    return IntegrationContext(
        log_manager=log_manager,
        doct_type=doc_type,
        request=request,
        user_id=user_id,
        client_id=client_id,
        progress_logger=progress_logger,
        progress=global_progress,
    )


async def handle_quickbooks_request(
    request: Request,
    doc_type: str,
    record_key: str,
    progress_event: str,
    current_user: dict,
    progress_logger: IntegrationsProgressLog,
    global_progress: dict,
    service_factory,  # Callable that returns the service instance
    send_method_getter,  # Callable that extracts the sending method (e.g., invoice_service.send_invoice)
):
    """
    Common handler for processing QuickBooks requests.
      - Retrieves client/user IDs.
      - Logs the operation.
      - Sets up the repository and integration context.
      - Parses JSON payload and normalizes records.
      - Processes records concurrently.
      - Emits a progress update back to the client.
    """
    client_id = request.session.get("client_id") or "Guest"
    user_id = current_user.get("userId")
    if not user_id:
        return JSONResponse(
            {"error": "User not authenticated or user id not found", "uploads": []},
            status_code=401,
        )

    # Clear previous log progress and send initial log message.
    progress_logger.clear()
    log_mgr = LogManager()
    await log_mgr.clear_queue()
    asyncio.create_task(
        log_mgr.send_log(
            f"⚙️ Processing {doc_type}...",
            log_key=f"{doc_type.lower()}-log-message",
            user_room=client_id,
        )
    )
    logger.info(f"Client ID: {client_id}")

    # Create integration context and repository
    repo = QuickBooksFirestoreService(user_id=user_id,integration="quickbooks")
    context = create_integration_context(
        log_manager=log_mgr,
        request=request,
        user_id=user_id,
        client_id=client_id,
        progress_logger=progress_logger,
        global_progress=global_progress,
        doc_type=doc_type,
    )
    qb_client = QuickBooksClientAdapter(context)
    service = service_factory(context, repo, qb_client)

    # Parse JSON payload
    try:
        data = await request.json()
    except Exception as e:
        error_msg = f"Failed to parse JSON: {str(e)}"
        logger.error(error_msg)
        return JSONResponse({"error": error_msg}, status_code=400)

    # Normalize payload. This function is assumed to be imported.
    try:
        normalized_records = await normalize_payload(data, record_key=record_key)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    # Process records concurrently
    send_func = send_method_getter(service)
    responses = await process_records(
        records=normalized_records,
        send_func=send_func,
        progress_logger=progress_logger,
        progress=global_progress,
    )

    await sio.emit(progress_event, {"progress": 100}, room=client_id)
    return JSONResponse(content=responses)