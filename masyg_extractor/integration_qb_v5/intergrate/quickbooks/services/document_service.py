import asyncio
import itertools
import json
from typing import List, Dict, Any, Iterable, Optional

from fastapi import Request

from masyg_extractor.integration_qb_v5.core.integration_context import IntegrationContext
from masyg_extractor.integration_qb_v5.domain.models import Item, Customer, Invoice, Document
from masyg_extractor.integration_qb_v5.entity_helper import EntityHelper
from masyg_extractor.integration_qb_v5.intergrate.baseAdapter import IntegrationClientAdapter
from masyg_extractor.integration_qb_v5.intergrate.quickbooks.services.audit_log_service import (
    AuditLogService,
    audit_op,  # used by subclasses, not here
)
from masyg_extractor.integration_qb_v5.intergrate.quickbooks.services.item_service import ItemService
from masyg_extractor.integration_qb_v5.repository.firestore_repository import QuickBooksFirestoreService
from masyg_extractor.integration_qb_v5.utils import safe_uuid_key
from masyg_extractor.integrations.utils import format_date
from masyg_extractor.integrations.xero.services.item_services import generate_sku
from masyg_extractor.services.log_manager import LogManager
from masyg_extractor.services.my_log import logger
from masyg_extractor.utils.extensions import sio
from masyg_extractor.utils.tool import get_original_filename
from masyg_extractor.integrations.transaction_helpers import generate_doc_number
from masyg_extractor.services.progress_log import IntegrationsProgressLog


def _is_retryable(payload: Dict[str, Any]) -> bool:
    """Simple retry classifier for common transient signals."""
    try:
        s = json.dumps(payload).lower()
    except Exception:
        s = str(payload).lower()
    return any(k in s for k in ["timeout", "network", "transport", "429", "rate", "limit"])


class DocumentService:
    def __init__(
        self,
        doc_number_prefix: str,
        doc_type: str,
        context: IntegrationContext,
        repo: QuickBooksFirestoreService,
        client: IntegrationClientAdapter,
    ):
        self.context = context
        self.doc_number_prefix = doc_number_prefix
        self.doc_type = doc_type
        self.repo = repo
        self.client = client
        self.item_service = ItemService(context, repo, client)
        self.customer_service = CustomerService(context, repo, client)
        self.entity_helper = EntityHelper(context, repo, client)

        self.audit = AuditLogService(context.user_id, integration="quickbooks")

    async def _log(self, message: str, level: str = "info") -> None:
        try:
            (logger.error if level.lower() == "error" else logger.info)(message)
            await self.context.log_manager.send_log(
                message,
                log_key="invoice-log-message",  # keep existing channel for now
                user_room=self.context.client_id,
            )
        except Exception as e:
            logger.error(f"Failed to send log: {message}. Error: {str(e)}")

    async def _record_exists(self, group_id: str, transaction_id: str) -> bool:
        try:
            return await asyncio.to_thread(
                self.repo.record_exists, f"{self.doc_type.lower()}s", group_id, transaction_id
            )
        except Exception as e:
            await self._log(f"Error checking record existence: {str(e)}", "error")
            return False

    async def store_records_in_firebase(self, records: List[Dict[str, Any]]) -> None:
        try:
            collection_name = f"{self.doc_type.lower()}s"
            tasks = [
                asyncio.to_thread(
                    self.repo.store_record,
                    collection_name,
                    record.get("group_id"),
                    record.get("transactionId"),
                    record,
                )
                for record in records
            ]
            if tasks:
                await asyncio.gather(*tasks)
            logger.info("All invoice records have been stored in Firebase.")
        except Exception as e:
            logger.error(f"Error storing records in Firebase: {str(e)}")

    @staticmethod
    def split_array_(input_map: Dict[str, Any], capacity: int) -> List[Dict[str, Any]]:
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
        Bulk flow:
          - dedupe/exists checks
          - bulk create customers/items
          - build batch payload (with bId per doc)
          - send to QB
          - per-item audit events (lean), batch envelope audit, and store successes
        """
        try:
            document_payload_bulk: List[Dict[str, Any]] = []
            invoice_records: Dict[str, Dict[str, Any]] = {}
            customers_map: Dict[str, Customer] = {}
            items_map: Dict[str, List[Item]] = {}
            existing_documents = 0

            for document in documents:
                if not document.group_id or not document.group_id.strip():
                    await self._log("Group ID is required for invoice creation.", "error")
                    continue

                if await self._record_exists(document.group_id, document.transaction_id):
                    dup_msg = (
                        f"{self.doc_type} for ({get_original_filename(document.transaction_id)}) "
                        f"already recorded in {self.repo.integration}."
                    )
                    await self._log(f"❌ {dup_msg}", "error")
                    existing_documents += 1
                    continue

                if not document.items:
                    await self._log("Items required for invoice creation.", "error")
                    continue

                key = safe_uuid_key(document.transaction_id)
                customers_map[key] = document.customer
                items_map[key] = document.items

            if len(documents) == existing_documents:
                raise Exception("No new documents to process.")

            # Bulk create customers and items (bucketed)
            split_customers, split_items = [], []
            customers_normalized = DocumentService.split_array_(customers_map, 30)
            items_normalized = DocumentService.split_array_(items_map, 30)

            for customers in customers_normalized:
                split_customers.append(await self.customer_service.create_customer_in_bulk(customers))
            for items in items_normalized:
                split_items.append(await self.item_service.create_item_in_bulk(items))

            customers_created = DocumentService.merge_buckets(split_customers)
            items_created = DocumentService.merge_buckets(split_items)

            # Build payloads per document
            for document in documents:
                try:
                    key = safe_uuid_key(document.transaction_id)
                    reference_items = items_created.get(key) or []
                    if not reference_items:
                        await self._log(
                            f"No items created for document {document.transaction_id}.", "error"
                        )
                        continue

                    line_items = []
                    for i in reference_items:
                        item_id = await self._ensure_item_id(i)
                        if not item_id:
                            await self._log(
                                f"❌ Skipping line; no ItemRef for '{i.name}' (sku={getattr(i, 'sku', '')}).",
                                "error",
                            )
                            continue

                        qty = int(i.quantity or 0)
                        unit_price = float(i.unit_price or 0.0)
                        amount = qty * unit_price

                        line_items.append({
                            "DetailType": "SalesItemLineDetail",
                            "Amount": amount,
                            "Description": i.description or "",
                            "SalesItemLineDetail": {
                                "ItemRef": {"value": str(item_id)},
                                "Qty": qty,
                                "UnitPrice": unit_price,
                                "TaxCodeRef": {"value": "TAX" if i.tax_code == "TAX" else "NON"},
                            },
                        })

                    if not line_items:
                        await self._log(
                            f"❌ No valid line items after resolution for document {document.transaction_id}.",
                            "error",
                        )
                        continue

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
                            "CustomerRef": {"value": valid_customer_id},
                            "AutoDocNumber": False,
                            "Line": line_items,
                            "TxnDate": format_date(document.date),
                            "CurrencyRef": {"value": "USD"},
                            "DocNumber": doc_number,
                        },
                        "operation": "create",
                        "bId": bid,
                    }
                    document_payload_bulk.append(payload)

                    invoice_records[bid] = {
                        "group_id": document.group_id,
                        "transactionId": document.transaction_id,
                        "integration": "quickbooks",
                        "transactionType": self.doc_type,
                        "docNumber": doc_number,
                        "customerId": valid_customer_id,
                        "date": document.date,
                        "bId": bid,
                        "amount": sum(
                            float(it.quantity or 0) * float(it.unit_price or 0) for it in document.items
                        ),
                        "metadata": {"syncToken": "0"},
                    }

                except Exception as e:
                    await self._log(
                        f"❌ Failed to process invoice for file {document.transaction_id}: {str(e)}", "error"
                    )

            if not document_payload_bulk:
                return {"error": "No valid documents processed."}

            # Optional: a batch envelope event (nice for visibility)
            batch_event_id = (
                f"{self.doc_type}:Batch:{documents[0].transaction_id if documents else '-'}:"
                f"{len(document_payload_bulk)}"
            )
            self.audit.start(
                event_id=batch_event_id,
                doc_type=self.doc_type,
                entity_type=self.doc_type,
                operation="batch_create",
                transaction_id=None,
                group_id=None,
                idempotency_key=None,
                payload={"BatchItemRequest": [p.get(self.doc_type, {}) for p in document_payload_bulk]},
            )

            # Start per-item PENDING events (so UI can show progress while waiting)
            for bid, inv in invoice_records.items():
                item_event_id = f"{self.doc_type}:{self.doc_type}:{inv['transactionId']}:{bid}"
                self.audit.start(
                    event_id=item_event_id,
                    doc_type=self.doc_type,
                    entity_type=self.doc_type,
                    operation="create",
                    transaction_id=inv["transactionId"],
                    group_id=inv["group_id"],
                    idempotency_key=bid,
                    payload=None,
                )

            # Send batch
            bulk_payload = {"BatchItemRequest": document_payload_bulk}
            quickbooks_response = await self.client.request(
                quickbooks_token=self.repo.get_integration_token(),
                payload=bulk_payload,
                endpoint="batch",
                method="POST",
            )

            response_payload = quickbooks_response.get("BatchItemResponse", []) or []
            firestore_records: List[Dict[str, Any]] = []

            for payload in response_payload:
                bid = payload.get("bId")
                inv = invoice_records.get(bid)
                if not inv:
                    continue

                item_event_id = f"{self.doc_type}:{self.doc_type}:{inv['transactionId']}:{bid}"
                file = get_original_filename(inv.get("transactionId"))

                if "Fault" in payload:
                    self.audit.fail(
                        event_id=item_event_id,
                        group_id=inv["group_id"],
                        transaction_id=inv["transactionId"],
                        error_category="Validation",
                        error_message="QuickBooks returned Fault",
                        error_details=None,
                        retryable=_is_retryable(payload),
                    )
                    await self._log(f"❌ Failed to create {inv.get('transactionType')} - document: {file}", "error")
                else:
                    self.audit.ok(
                        event_id=item_event_id,
                        group_id=inv["group_id"],
                        transaction_id=inv["transactionId"],
                    )
                    await self._log(
                        f"✅ {self.doc_type.capitalize()} processed successfully - document: {file}"
                    )
                    firestore_records.append(inv)

            if firestore_records:
                await self.store_records_in_firebase(firestore_records)

            any_success = any("Fault" not in p for p in response_payload)
            if any_success:
                self.audit.ok(batch_event_id, group_id=None, transaction_id=None)
            else:
                self.audit.fail(
                    batch_event_id, None, None, "Unknown", "All items failed", quickbooks_response, retryable=True
                )

            await sio.emit("quickbooks-invoice-progress", {"progress": 100}, room=self.context.client_id)
            return quickbooks_response

        except Exception as e:
            error_msg = f"❌ Error in bulk sending of {self.doc_type} documents: {str(e)}"
            await sio.emit("quickbooks-invoice-progress", {"progress": 100}, room=self.context.client_id)
            await self._log(error_msg, "error")
            return {"error": str(e)}

    async def _ensure_item_id(self, i: Item) -> Optional[str]:
        """
        Ensure the Item has an Id:
          1) try by Sku
          2) try by Name
          3) create single item as last resort
        """
        if getattr(i, "id", None):
            return str(i.id)

        # 1) lookup by Sku
        if getattr(i, "sku", None):
            query = f"SELECT * FROM Item WHERE Sku = '{i.sku}'"
            resp = await self.client.request(
                self.repo.get_integration_token(),
                "query",
                method="GET",
                params={"query": query},
            )
            found = resp.get("QueryResponse", {}).get("Item", [])
            if found:
                i.id = found[0].get("Id")
                return str(i.id)

        # 2) lookup by Name
        if getattr(i, "name", None):
            qname = i.name.replace("'", "''")
            query = f"SELECT * FROM Item WHERE Name = '{qname}'"
            resp = await self.client.request(
                self.repo.get_integration_token(),
                "query",
                method="GET",
                params={"query": query},
            )
            found = resp.get("QueryResponse", {}).get("Item", [])
            if found:
                i.id = found[0].get("Id")
                return str(i.id)

        # 3) create single item quickly
        new_id = await self.item_service.create_item(i)
        if new_id:
            i.id = new_id
            return str(i.id)

        return None

    async def send_document(self, document: Document, share_progress: float) -> Dict[str, Any] | str:
        """
        Creates a single document (Invoice or SalesReceipt depending on subclass usage).
        NOTE: The audit decorator should be applied in subclasses so doc_type is correct.
        """
        log_manager = LogManager()
        try:
            await log_manager.clear_queue()

            # Progress updates
            for step in range(5):
                await asyncio.sleep(0.3)
                self.context.progress[f"creating_{self.doc_type}"] = (
                    (step + 1) / 5
                ) * IntegrationsProgressLog.CREATING_ITEM_WEIGHT
                await self.context.progress_logger.safe_emit_progress(self.context.progress)

            if not document.group_id or not document.group_id.strip():
                return {"error": "Group ID is required for invoice creation."}

            if await self._record_exists(document.group_id, document.transaction_id):
                msg = (
                    f"{self.doc_type} for ({get_original_filename(document.transaction_id)}) "
                    f"already recorded in {self.repo.integration}."
                )
                await asyncio.sleep(1)
                await self.context.log_manager.send_log(
                    f"❌ {msg}",
                    log_key=f"{self.doc_type.lower()}-log-message",
                    user_room=self.context.client_id,
                )
                await asyncio.sleep(1)
                raise Exception(msg)

            valid_customer_id = await self.customer_service.get_or_create_customer(document.customer)
            if not document.items:
                logger.info("No items provided for document.")
                return {"error": "Items required for document creation."}

            line_items = []
            for item in document.items:
                # ensure item exists or create
                if not await self.item_service.check_item_exists(item):
                    new_id = await self.item_service.create_item(item)
                    if new_id:
                        item.id = new_id
                    else:
                        logger.error("Failed to create item: Received invalid item ID")
                qty = int(item.quantity or 0)
                unit_price = float(item.unit_price or 0.0)
                amount = qty * unit_price
                tax_code = item.tax_code

                line_items.append({
                    "DetailType": "SalesItemLineDetail",
                    "Amount": amount,
                    "Description": item.description or "",
                    "SalesItemLineDetail": {
                        # If you want to include ItemRef after creating/ensuring, add it here
                        # "ItemRef": {"value": str(item.id)} if item.id else None,
                        "Qty": qty,
                        "UnitPrice": unit_price,
                        "TaxCodeRef": {"value": tax_code},
                    },
                })

            date = document.date
            doc_number = generate_doc_number(self.doc_number_prefix)
            customer_name = document.customer.name

            qb_payload = {
                "CustomerRef": {"value": valid_customer_id, "name": customer_name},
                "AutoDocNumber": False,
                "Line": line_items,
                "TxnDate": format_date(date),
                "CurrencyRef": {"value": "USD"},
                "DocNumber": doc_number,
            }
            payload = {"BatchItemRequest": [qb_payload]}

            document_id = await self.entity_helper.create_entity(self.doc_type, payload)

            if self.context.user_id:
                total_amount = sum(float(it.quantity or 0) * float(it.unit_price or 0) for it in document.items)
                document_record = {
                    "integration": "quickbooks",
                    "transactionType": self.doc_type,
                    "transactionId": document.transaction_id,
                    "docNumber": doc_number,
                    "customerId": valid_customer_id,
                    "date": document.date,
                    "amount": total_amount,
                    "metadata": {"syncToken": "0"},
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
                    f"✅ {self.doc_type.capitalize()} sent and processed "
                    f"{get_original_filename(document.transaction_id)} for {document.customer.name} successfully"
                )

            return document_id

        except Exception as e:
            error_msg = f"❌ Failed to create {self.doc_type.lower()} for file {document.transaction_id}: {str(e)}"
            await sio.emit("quickbooks-invoice-progress", {"progress": 100}, room=self.context.client_id)
            await self._log(error_msg, "error")
            return {"error": str(e)}
