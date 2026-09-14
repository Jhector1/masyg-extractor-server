import asyncio
from pprint import pprint
from typing import List, Dict, Any

from masyg_extractor.integrations.accounting.shared.identifiers import safe_uuid_key
from masyg_extractor.integrations.accounting.shared.operation_progress import AccountingOperationProgress
from masyg_extractor.integrations.accounting.core.integration_context import IntegrationContext
from masyg_extractor.integrations.accounting.core.models import Document
from masyg_extractor.integrations.accounting.xero.entity_helper import EntityHelper
from masyg_extractor.integrations.accounting.xero.base_adapter import IntegrationClientAdapter
from masyg_extractor.integrations.accounting.xero.services.item_service import ItemService
from masyg_extractor.integrations.accounting.shared.firestore_repository import QuickBooksFirestoreService
from masyg_extractor.integrations.accounting.xero.utils import extract_uuid
from masyg_extractor.integrations.utils import format_date
from masyg_extractor.services.log_manager import LogManager
from masyg_extractor.services.my_log import logger
from masyg_extractor.integrations.accounting.xero.services.customer_service import CustomerService
from masyg_extractor.integrations.transaction_helpers import generate_doc_number
from masyg_extractor.services.progress_log import IntegrationsProgressLog
from masyg_extractor.utils.extensions import sio
from masyg_extractor.utils.tool import get_original_filename

#print

def _xero_response_invoice_error(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None

    messages: list[str] = []
    validation_errors = payload.get("ValidationErrors")
    if isinstance(validation_errors, list):
        for entry in validation_errors:
            if not isinstance(entry, dict):
                continue
            message = str(entry.get("Message") or "").strip()
            if message and message not in messages:
                messages.append(message)

    if payload.get("HasErrors") and not messages:
        message = str(payload.get("Message") or "").strip()
        if message:
            messages.append(message)

    return "; ".join(messages) if messages else None

class DocumentService:
    def __init__(self, doc_number_prefix: str, doc_type: str,  context: IntegrationContext,
                 repo: QuickBooksFirestoreService, client: IntegrationClientAdapter):
        self.context = context
        self.doc_number_prefix = doc_number_prefix
        self.doc_type = doc_type
        self.repo = repo
        self.client = client
        self.item_service = ItemService(context, repo, client)
        self.customer_service = CustomerService(context, repo, client)
        self.entity_helper = EntityHelper(context, repo, client)

    async def _log(self, message: str, level: str = "info") -> None:
        """
        A helper method for logging messages asynchronously.
        """
        try:
            if level.lower() == "error":
                logger.error(message)
            else:
                logger.info(message)
            await self.context.log_manager.send_log(
                message,
                log_key=f"{self.doc_type.lower()}-log-message",
                user_room=self.context.client_id
            )
        except Exception as e:
            # Fallback logging if asynchronous log fails.
            logger.error(f"Failed to send log: {message}. Error: {str(e)}")

    async def _record_exists(self, group_id: str, transaction_id: str) -> bool:
        """
        Check if a document record already exists in the repository.
        """
        try:
            return await asyncio.to_thread(
                self.repo.record_exists,
                f"{self.doc_type.lower()}s",
                group_id,
                transaction_id
            )
        except Exception as e:
            await self._log(f"Error checking record existence: {str(e)}", "error")
            return False

    async def store_records_in_firebase(self, records: List[Dict[str, Any]]) -> None:
        """Stores all invoice records in Firebase concurrently."""
        try:
            collection_name = f"{self.doc_type.lower()}s"
            tasks = [
                asyncio.to_thread(
                    self.repo.store_record,
                    collection_name,
                    record.get("group_id"),
                    record.get("transactionId"),
                    record
                ) for record in records
            ]
            if tasks:
                await asyncio.gather(*tasks)
            logger.info("All invoice records have been stored in Firebase.")
        except Exception as e:
            logger.error(f"Error storing records in Firebase: {str(e)}")

    async def send_document_in_bulk(self, documents: List[Document], share_progress: float,invoice_status='ACCREC') -> Dict[str, Any]:
        """
        Processes a list of documents in bulk:
          - Performs a duplicate check.
          - Accumulates customer and item data for bulk creation.
          - Prepares payloads for invoice creation.
          - Sends invoices via the integration client.
          - Stores the processed invoice records in Firebase.
        """
        operation_progress = AccountingOperationProgress.from_context(
            self.context,
            provider="xero",
            fallback_action=f"create-{self.doc_type.lower()}",
        )
        await operation_progress.queue_documents(
            [
                (document.transaction_id, get_original_filename(document.transaction_id))
                for document in documents
            ]
        )
        await operation_progress.mark_all_running(
            progress=10,
            message="Preparing document",
        )

        try:
            document_payload_bulk = []
            invoice_records = []
            prepared_documents = []
            customers_map, items_map = {}, {}
            existing_documents = 0

            # Process each document.
            count=1
            for document in documents:
                count += 1
                if not document.group_id or not document.group_id.strip():
                    await self._log("Group ID is required for invoice creation.", "error")
                    await operation_progress.failed(
                        document.transaction_id,
                        filename=get_original_filename(document.transaction_id),
                        error="Group ID is required.",
                    )
                    continue

                if await self._record_exists(document.group_id, document.transaction_id):
                    dup_msg = (f"{self.doc_type} for ({get_original_filename(document.transaction_id)}) "
                               "already recorded in Xero.")
                    await self._log(f"❌ {dup_msg}", "error")
                    await operation_progress.failed(
                        document.transaction_id,
                        filename=get_original_filename(document.transaction_id),
                        error=dup_msg,
                    )
                    existing_documents += 1
                    continue

                if not document.items:
                    await self._log("Items required for invoice creation.", "error")
                    await operation_progress.failed(
                        document.transaction_id,
                        filename=get_original_filename(document.transaction_id),
                        error="Items are required.",
                    )
                    continue

                key = safe_uuid_key(document.transaction_id)
                customers_map[key] = document.customer
                items_map[key] = document.items


            if len(documents) == existing_documents:
                await operation_progress.fail_remaining(
                    "No new documents to process."
                )
                await sio.emit(
                    "xero-invoice-progress",
                    {"progress": 100},
                    room=self.context.client_id,
                )
                return operation_progress.result_payload()

            # Create customers and items in bulk.
            customers_created = await self.customer_service.create_customer_in_bulk(customers_map)
            items_created = await self.item_service.create_item_in_bulk(items_map)

            # Build payloads for each document.
            # Build payloads for each document.
            for document in documents:
                try:
                    key = safe_uuid_key(document.transaction_id)
                    # extract_uuid Use a default empty list if no items were created for this key.
                    reference_items = items_created.get(key) or []
                    if not reference_items:
                        await self._log(
                            f"No items created for document {document.transaction_id}.", "error"
                        )
                        await operation_progress.failed(
                            document.transaction_id,
                            filename=get_original_filename(document.transaction_id),
                            error="Items could not be prepared.",
                        )
                        continue

                    line_items = [{
                        "Description": item.description or "",
                        "Quantity": int(item.quantity or 0),
                        "UnitAmount": float(item.unit_price or 0),
                        "AccountCode": "300",
                        "ItemCode": item.id, #using the item code as a placeholder for real ids
                        "TaxType": "OUTPUT" if item.tax_code == "TAX" else "NONE"
                    } for item in reference_items]

                    valid_customer = customers_created.get(key) or []
                    if not valid_customer:
                        await self._log(
                            f"Customer creation failed for document {document.transaction_id}.", "error"
                        )
                        await operation_progress.failed(
                            document.transaction_id,
                            filename=get_original_filename(document.transaction_id),
                            error="Customer could not be created.",
                        )
                        continue
                    valid_customer_id = valid_customer.id
                    doc_number = generate_doc_number(self.doc_number_prefix)
                    payload = {
                        "Type": invoice_status,
                        "Contact": {"ContactID": valid_customer_id},
                        "Date": format_date(document.date),
                        "DueDate": format_date(document.due_date),
                        "LineItems": line_items,
                        "Status": "DRAFT",
                        "CurrencyCode": "USD",
                        "InvoiceNumber": doc_number,
                    }
                    document_payload_bulk.append(payload)
                    prepared_documents.append(document)
                    await operation_progress.update(
                        document.transaction_id,
                        status="running",
                        progress=65,
                        filename=get_original_filename(document.transaction_id),
                        message="Ready to send to Xero",
                    )

                    invoice_record = {
                        "group_id": document.group_id,
                        "transactionId": document.transaction_id,
                        "integration": "xero",
                        "transactionType": self.doc_type,
                        "docNumber": doc_number,
                        "customerId": valid_customer_id,
                        "date": document.date,
                        "amount": sum(
                            float(item.quantity or 0) * float(item.unit_price or 0) for item in document.items),
                        "metadata": {"syncToken": "0"}
                    }
                    invoice_records.append(invoice_record)
                except Exception as e:
                    await operation_progress.failed(
                        document.transaction_id,
                        filename=get_original_filename(document.transaction_id),
                        error=str(e),
                    )
                    await self._log(f"❌ Failed to process invoice for file {document.transaction_id}: {str(e)}",
                                    "error")

            if document_payload_bulk:
                bulk_payload = {"Invoices": document_payload_bulk}

                xero_response = await self.client.request(
                    xero_token=self.repo.get_integration_token(),
                    payload=bulk_payload,
                    endpoint="Invoices",
                    method="POST"
                )

                provider_error_by_index: dict[int, str] = {}
                raw_document_errors = xero_response.get("document_errors")
                if isinstance(raw_document_errors, list):
                    for entry in raw_document_errors:
                        if not isinstance(entry, dict):
                            continue
                        try:
                            index = int(entry.get("index"))
                        except (TypeError, ValueError):
                            continue
                        message = str(entry.get("message") or "").strip()
                        if message:
                            provider_error_by_index[index] = message

                successful_records = []
                response_invoices = xero_response.get("Invoices")
                if not isinstance(response_invoices, list):
                    response_invoices = []

                if "error" in xero_response:
                    default_error = str(
                        xero_response.get("error")
                        or "Xero rejected this document."
                    ).strip()
                    for index, document in enumerate(prepared_documents):
                        error_message = provider_error_by_index.get(
                            index,
                            default_error or "Xero rejected this document.",
                        )
                        await operation_progress.failed(
                            document.transaction_id,
                            filename=get_original_filename(document.transaction_id),
                            error=error_message,
                        )
                        await self._log(
                            f"❌ Failed to create {self.doc_type} "
                            f"{get_original_filename(document.transaction_id)} in Xero: "
                            f"{error_message}",
                            "error",
                        )
                else:
                    for index, document in enumerate(prepared_documents):
                        provider_invoice = (
                            response_invoices[index]
                            if index < len(response_invoices)
                            else None
                        )
                        provider_error = _xero_response_invoice_error(
                            provider_invoice
                        )
                        if provider_error:
                            await operation_progress.failed(
                                document.transaction_id,
                                filename=get_original_filename(document.transaction_id),
                                error=provider_error,
                            )
                            await self._log(
                                f"❌ Xero rejected {self.doc_type} "
                                f"{get_original_filename(document.transaction_id)}: "
                                f"{provider_error}",
                                "error",
                            )
                            continue

                        if index < len(invoice_records):
                            successful_records.append(invoice_records[index])

                        await operation_progress.succeeded(
                            document.transaction_id,
                            filename=get_original_filename(document.transaction_id),
                            message="Created in Xero",
                        )
                        await self._log(
                            f"✅ {self.doc_type.capitalize()} created in Xero for "
                            f"{document.customer.name} "
                            f"({get_original_filename(document.transaction_id)})"
                        )

                    if successful_records:
                        await self.store_records_in_firebase(successful_records)

                await operation_progress.fail_remaining(
                    "Xero did not return a result for this document."
                )
                await sio.emit(
                    "xero-invoice-progress",
                    {"progress": 100},
                    room=self.context.client_id,
                )
                return operation_progress.result_payload()

            await operation_progress.fail_remaining(
                "No valid documents were processed."
            )
            return operation_progress.result_payload()

        except Exception as e:
            error_message = str(e).strip() or "Xero operation failed."
            await operation_progress.fail_remaining(error_message)
            await sio.emit(
                "xero-invoice-progress",
                {"progress": 100},
                room=self.context.client_id,
            )
            await self._log(
                f"❌ Error in bulk sending of {self.doc_type} documents: "
                f"{error_message}",
                "error",
            )
            return operation_progress.result_payload()

    async def send_document(self, document: Document, share_progress: float) -> Dict[str, Any] or str:
        """
        Creates an invoice asynchronously in QuickBooks and stores key invoice information in Firestore.
        """
        log_manager = LogManager()
        try:
            await log_manager.clear_queue()

            # Update progress asynchronously.
            for step in range(5):
                await asyncio.sleep(0.3)
                self.context.progress[f"creating_{self.doc_type}"] = ((step + 1) / 5) * IntegrationsProgressLog.CREATING_ITEM_WEIGHT
                await self.context.progress_logger.safe_emit_progress(share_progress)

            if not document.group_id or not document.group_id.strip():
                return {"error": "Group ID is required for invoice creation."}

            if await self._record_exists(document.group_id, document.transaction_id):
                msg = f"{self.doc_type} for ({get_original_filename(document.transaction_id)}) already recorded in Xero."
                await asyncio.sleep(1)
                await self.context.log_manager.send_log(
                    f"❌ {msg}",
                    log_key=f"{self.doc_type.lower()}-log-message",
                    user_room=self.context.client_id
                )
                await asyncio.sleep(1)
                raise Exception(msg)

            valid_customer_id = await self.customer_service.get_or_create_customer(document.customer)
            if not document.items:
                logger.info("No items provided for invoice.")
                return {"error": "Items required for invoice creation."}

            line_items = []
            for item in document.items:
                if not await self.item_service.check_item_exists(item):
                    new_id = await self.item_service.create_item(item)
                    if new_id:
                        item.id = new_id
                    else:
                        logger.error("Failed to create item: Received invalid item ID")
                line_items.append({
                    "Description": item.description or "",
                    "Quantity": int(item.quantity or 0),
                    "UnitAmount": float(item.unit_price or 0),
                    "AccountCode": "700",
                    "TaxType": "OUTPUT" if item.tax_code == "TAX" else "NONE",
                })

            doc_number = generate_doc_number(self.doc_number_prefix)
            payload = {
                "Type": "ACCREC",
                "Contact": {"ContactID": valid_customer_id},
                "Date": format_date(document.date),
                "DueDate": format_date(document.date),
                "LineItems": line_items,
                "Status": "AUTHORISED",
                "CurrencyCode": "USD"
            }
            payload = {"Invoices": [payload]}

            document_id = await self.entity_helper.create_entity(self.doc_type, payload)

            if self.context.user_id:
                total_amount = sum(float(item.quantity or 0) * float(item.unit_price or 0) for item in document.items)
                document_record = {
                    "integration": "quickbooks",
                    "transactionType": self.doc_type,
                    "transactionId": document.transaction_id,
                    "docNumber": doc_number,
                    "customerId": valid_customer_id,
                    "date": document.date,
                    "amount": total_amount,
                    "metadata": {"syncToken": "0"}
                }
                await asyncio.to_thread(
                    self.repo.store_record,
                    f"{self.doc_type.lower()}s",
                    document.group_id,
                    document.transaction_id,
                    document_record,
                )

            if document_id and int(document_id) > 0:
                await self._log(
                    f"✅ {self.doc_type.capitalize()} sent and processed {get_original_filename(document.transaction_id)} for {document.customer.name} successfully"
                )

            return document_id

        except Exception as e:
            error_msg = f"❌ Failed to create invoice for file {document.transaction_id}: {str(e)}"
            await self._log(error_msg, "error")
            return {"error": str(e)}
