import asyncio
from itertools import chain
from typing import List, Dict, Any
from fastapi import Request

from masyg_extractor.integration_v4.core.integration_context import IntegrationContext
from masyg_extractor.integration_v4.domain.models import Item, Customer, Invoice, Document
from masyg_extractor.integration_v4.entity_helper import EntityHelper
from masyg_extractor.integration_v4.intergrate.baseAdapter import IntegrationClientAdapter
from masyg_extractor.integration_v4.intergrate.quickbooks.services.item_service import ItemService
from masyg_extractor.integration_v4.repository.firestore_repository import QuickBooksFirestoreService
from masyg_extractor.integration_v4.utils import extract_uuid
from masyg_extractor.integrations.utils import format_date
from masyg_extractor.integrations.xero.xero_router import get_items
from masyg_extractor.services.log_manager import LogManager
from masyg_extractor.services.my_log import logger, send_log
from masyg_extractor.integrations.quickbooks.quickbooks_client import quickbooks_request
from masyg_extractor.integrations.quickbooks.repository.firestore_repository import (
    store_invoice_record,
    invoice_exists_in_firestore
)
from masyg_extractor.integration_v4.intergrate.quickbooks.services.customer_service import CustomerService
from masyg_extractor.integrations.quickbooks.services.item_service import check_item_exists, create_item
from masyg_extractor.integrations.transaction_helpers import generate_doc_number, check_duplicate_record
from masyg_extractor.services.progress_log import IntegrationsProgressLog
from masyg_extractor.utils.extensions import sio
from masyg_extractor.utils.tool import get_original_filename

#print
class DocumentService:
    def __init__(self, doc_number_prefix: str, doc_type: str, context: IntegrationContext,
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
        try:
            document_payload_bulk = []
            invoice_records = []
            customers_map, items_map = {}, {}
            existing_documents = 0

            # Process each document.
            for document in documents:
                if not document.group_id or not document.group_id.strip():
                    await self._log("Group ID is required for invoice creation.", "error")
                    continue

                if await self._record_exists(document.group_id, document.transaction_id):
                    dup_msg = (f"{self.doc_type} for ({get_original_filename(document.transaction_id)}) "
                               "already recorded in Xero.")
                    await self._log(f"❌ {dup_msg}", "error")
                    existing_documents += 1
                    continue

                if not document.items:
                    await self._log("Items required for invoice creation.", "error")
                    continue

                key = extract_uuid(document.transaction_id)[:20]
                customers_map[key] = document.customer
                items_map[key] = document.items

            if len(customers_map) == existing_documents:
                raise Exception("No new documents to process.")

            # Create customers and items in bulk.
            customers_created = await self.customer_service.create_customer_in_bulk(customers_map)
            items_created = await self.item_service.create_item_in_bulk(items_map)

            # Build payloads for each document.
            # Build payloads for each document.
            for document in documents:
                try:
                    key = extract_uuid(document.transaction_id)[:20]
                    # Use a default empty list if no items were created for this key.
                    reference_items = items_created.get(key) or []
                    if not reference_items:
                        await self._log(
                            f"No items created for document {document.transaction_id}.", "error"
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
                    await sio.emit("xero-invoice-progress", {"progress": 100}, room=self.context.client_id)

                    invoice_record = {
                        "group_id": document.group_id,
                        "transactionId": document.transaction_id,
                        "integration": "quickbooks",
                        "transactionType": self.doc_type,
                        "docNumber": doc_number,
                        "customerId": valid_customer_id,
                        "date": document.date,
                        "amount": sum(
                            float(item.quantity or 0) * float(item.unit_price or 0) for item in document.items),
                        "metadata": {"syncToken": "0"}
                    }
                    invoice_records.append(invoice_record)
                    await self._log(
                        f"✅ {self.doc_type.capitalize()} processed for {document.customer.name} successfully")
                except Exception as e:
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
                if "error" not in xero_response:
                    await self.store_records_in_firebase(invoice_records)
                return xero_response

            return {"error": "No valid documents processed."}

        except Exception as e:
            error_msg = f"❌ Error in bulk sending of {self.doc_type} documents: {str(e)}"
            await sio.emit("xero-invoice-progress", {"progress": 100}, room=self.context.client_id)

            await self._log(error_msg, "error")

            return {"error": str(e)}

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
