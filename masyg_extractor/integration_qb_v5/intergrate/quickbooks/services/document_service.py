import asyncio
import itertools
from itertools import chain
from pprint import pprint
from typing import List, Dict, Any, Iterable
from fastapi import Request
# from sympy.physics.units import amount

from masyg_extractor.integration_qb_v5.core.integration_context import IntegrationContext
from masyg_extractor.integration_qb_v5.domain.models import Item, Customer, Invoice, Document
from masyg_extractor.integration_qb_v5.entity_helper import EntityHelper
from masyg_extractor.integration_qb_v5.intergrate.baseAdapter import IntegrationClientAdapter
from masyg_extractor.integration_qb_v5.intergrate.quickbooks.services.item_service import ItemService
from masyg_extractor.integration_qb_v5.repository.firestore_repository import QuickBooksFirestoreService
from masyg_extractor.integration_qb_v5.utils import extract_uuid
from masyg_extractor.integrations.utils import format_date
from masyg_extractor.integrations.xero.services.item_services import generate_sku
from masyg_extractor.integrations.xero.xero_router import get_items
from masyg_extractor.services.log_manager import LogManager
from masyg_extractor.services.my_log import logger, send_log
from masyg_extractor.integrations.quickbooks.quickbooks_client import quickbooks_request
from masyg_extractor.integrations.quickbooks.repository.firestore_repository import (
    store_invoice_record,
    invoice_exists_in_firestore
)
from masyg_extractor.integration_qb_v5.intergrate.quickbooks.services.customer_service import CustomerService
from masyg_extractor.integrations.quickbooks.services.item_service import check_item_exists, create_item
from masyg_extractor.integrations.transaction_helpers import generate_doc_number, check_duplicate_record
from masyg_extractor.services.progress_log import IntegrationsProgressLog
from masyg_extractor.utils.extensions import sio
from masyg_extractor.utils.tool import get_original_filename


# print
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
                log_key="invoice-log-message",  # f"{self.doc_type.lower()}-log-message",
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

    @staticmethod
    def split_array_(input_map: Dict[str, Any], capacity: int) -> List[Dict[str, Any]]:
        """
        Take each key→value in input_map (value can be iterable or single object)
        and pack its items into buckets of total length ≤ capacity.
        Non-iterable values (or str/bytes) are treated as single-item scalars, not lists.

        Args:
            input_map: mapping from str to any object or iterable of objects.
            capacity: maximum number of items per bucket.

        Returns:
            List of buckets, where each bucket is a dict mapping keys to either
            a scalar (for single-object inputs) or lists of objects.
        """
        result: List[Dict[str, Any]] = []
        bucket: Dict[str, Any] = {}
        used = 0

        for key, value in input_map.items():
            if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
                it = iter(value)
                is_scalar = False
            else:
                it = iter([value])
                is_scalar = True

            while True:
                if used == capacity:
                    result.append(bucket)
                    bucket = {}
                    used = 0

                to_take = capacity - used
                chunk = list(itertools.islice(it, to_take))
                if not chunk:
                    break

                if is_scalar:
                    # scalar inputs always chunk len=1
                    bucket[key] = chunk[0]
                else:
                    if key in bucket:
                        bucket[key].extend(chunk)
                    else:
                        bucket[key] = chunk.copy()
                used += len(chunk)

        if bucket:
            result.append(bucket)
        return result

    @staticmethod
    def merge_buckets(buckets: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Reconstruct the original mapping from a list of bucket dicts produced by split_array.

        Args:
            buckets: list of bucket dicts (as returned by split_array).

        Returns:
            A dict mapping each key to either a single value or a concatenated list of values.
        """
        merged: Dict[str, Any] = {}
        for bucket in buckets:
            for key, items in bucket.items():
                if key not in merged:
                    merged[key] = items
                else:
                    existing = merged[key]
                    if isinstance(existing, list) and isinstance(items, list):
                        existing.extend(items)
                    elif isinstance(existing, list):
                        existing.append(items)
                    elif isinstance(items, list):
                        merged[key] = [existing] + items
                    else:
                        merged[key] = [existing, items]
        return merged

    async def send_document_in_bulk(self, documents: List[Document], share_progress: float) -> Dict[str, Any]:
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
            invoice_records = {}
            customers_map, items_map = {}, {}
            existing_documents = 0

            # Process each document.
            for document in documents:
                if not document.group_id or not document.group_id.strip():
                    await self._log("Group ID is required for invoice creation.", "error")
                    continue

                if await self._record_exists(document.group_id, document.transaction_id):
                    dup_msg = (f"{self.doc_type} for ({get_original_filename(document.transaction_id)}) "
                               f"already recorded in {self.repo.integration}.")
                    await self._log(f"❌ {dup_msg}", "error")
                    existing_documents += 1
                    continue

                if not document.items:
                    await self._log("Items required for invoice creation.", "error")
                    continue

                key = extract_uuid(document.transaction_id)[:20]
                customers_map[key] = document.customer
                items_map[key] = document.items

            if len(documents) == existing_documents:
                raise Exception("No new documents to process.")

            # Create customers and items in bulk.
            split_customers = []
            split_items = []


            customers_normalized = DocumentService.split_array_(customers_map, 30)
            items_normalized = DocumentService.split_array_(items_map, 30)


            for customers in customers_normalized:

                split_customers.append(await self.customer_service.create_customer_in_bulk(customers))
            for items in items_normalized:
                split_items.append(await self.item_service.create_item_in_bulk(items))

            customers_created = DocumentService.merge_buckets(split_customers)
            items_created = DocumentService.merge_buckets(
                split_items)  # await self.item_service.create_item_in_bulk(items_map)
            # print("Customer created", customers_created)
            # print("Item created", items_created)
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
                    # amount = int(item.quantity)*float(item.unit_price or 0)
                    line_items = [{
                        "DetailType": "SalesItemLineDetail",
                        "Amount": int(item.quantity) * float(item.unit_price or 0),
                        "Description": item.description if item.description else "",
                        "SalesItemLineDetail": {
                            "ItemRef": {"value": str(item.id)},
                            "Qty": int(item.quantity or 0),
                            "UnitPrice": float(item.unit_price or 0),
                            "TaxCodeRef": {
                                "value": "TAX" if item.tax_code == "TAX" else "NON"
                            },

                        }
                    } for item in reference_items]

                    valid_customer = customers_created.get(key) or None
                    if not valid_customer:
                        await self._log(
                            f"Customer creation failed for document {document.transaction_id}.", "error"
                        )
                        continue
                    valid_customer_id = valid_customer.id
                    doc_number = generate_doc_number(self.doc_number_prefix)
                    bid = generate_sku(document.transaction_id)
                    payload = {
                        self.doc_type: {
                            "CustomerRef": {"value": valid_customer_id
                                            # "name": valid_customer.name

                                            },
                            "AutoDocNumber": False,
                            # "EmailStatus": "NotSet",
                            "Line": line_items,
                            # "TotalAmt": total_amount,
                            "TxnDate": format_date(document.date),
                            "CurrencyRef": {"value": "USD"},
                            # "PrintStatus": "NeedToPrint",
                            "DocNumber": doc_number
                        },
                        "operation": "create",
                        "bId": bid,
                    }
                    document_payload_bulk.append(payload)

                    invoice_record =  {
                        "group_id": document.group_id,
                        "transactionId": document.transaction_id,
                        "integration": "quickbooks",
                        "transactionType": self.doc_type,
                        "docNumber": doc_number,
                        "customerId": valid_customer_id,
                        "date": document.date,
                        "bId": bid,
                        "amount": sum(
                            float(item.quantity or 0) * float(item.unit_price or 0) for item in document.items),
                        "metadata": {"syncToken": "0"}
                    }
                    invoice_records[bid]=invoice_record

                except Exception as e:
                    await self._log(f"❌ Failed to process invoice for file {document.transaction_id}: {str(e)}",
                                    "error")

            if document_payload_bulk:
                bulk_payload = {"BatchItemRequest": document_payload_bulk}
                pprint(bulk_payload)
                quickbooks_response = await self.client.request(
                    quickbooks_token=self.repo.get_integration_token(),
                    payload=bulk_payload,
                    endpoint="batch",
                    method="POST"
                )
                pprint(quickbooks_response)
                response_payload = quickbooks_response.get("BatchItemResponse", {})
                firestore_records=[]
                for payload in response_payload:
                    bid = payload.get("bId")
                    invoice_record= invoice_records.get(bid)
                    file = get_original_filename(invoice_record.get('transactionId'))
                    if 'Fault' in payload:
                        error_msg = f"❌ Failed to create {invoice_record.get('transactionType')} - document: {file}"
                        await self._log(error_msg, "error")
                    else:
                        await self._log(
                            f"✅ {self.doc_type.capitalize()} processed  successfully - document: {file}")
                        firestore_records.append(invoice_record)



                if len(firestore_records) > 0:

                    await self.store_records_in_firebase(firestore_records)
                await sio.emit("quickbooks-invoice-progress", {"progress": 100}, room=self.context.client_id)

                return quickbooks_response

            return {"error": "No valid documents processed."}

        except Exception as e:
            error_msg = f"❌ Error in bulk sending of {self.doc_type} documents: {str(e)}"
            await sio.emit("quickbooks-invoice-progress", {"progress": 100}, room=self.context.client_id)

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
                msg = f"{self.doc_type} for ({get_original_filename(document.transaction_id)}) already recorded in ${self.repo.integration}."
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
                quantity = item.quantity if item.quantity else 0
                unit_price = item.unit_price if item.unit_price else 0
                amount = float(quantity) * float(unit_price)
                # total_amount += amount
                tax_code = item.tax_code
                line_items.append({
                    "DetailType": "SalesItemLineDetail",
                    "Amount": amount,
                    "Description": item.description if item.description else "",
                    "SalesItemLineDetail": {
                        # "ItemRef": {"value": str(new_id)},
                        "Qty": int(quantity),
                        "UnitPrice": float(unit_price),
                        "TaxCodeRef": {"value": tax_code}
                    }
                })
            date = document.date
            doc_number = generate_doc_number(self.doc_number_prefix)
            customer_name = document.customer.name

            payload = {
                "CustomerRef": {"value": valid_customer_id, "name": customer_name},
                "AutoDocNumber": False,
                # "EmailStatus": "NotSet",
                "Line": line_items,
                # "TotalAmt": total_amount,
                "TxnDate": format_date(date),
                "CurrencyRef": {"value": "USD"},
                # "PrintStatus": "NeedToPrint",
                "DocNumber": doc_number
            }
            payload = {"BatchItemRequest": [payload]}

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
            await sio.emit("quickbooks-invoice-progress", {"progress": 100}, room=self.context.client_id)

            await self._log(error_msg, "error")
            return {"error": str(e)}
