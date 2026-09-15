import asyncio
import itertools
import json
from typing import List, Dict, Any, Iterable, Optional

from fastapi import Request

from masyg_extractor.integrations.accounting.core.integration_context import IntegrationContext
from masyg_extractor.integrations.accounting.core.models import Item, Customer, Invoice, Document
from masyg_extractor.integrations.accounting.quickbooks.entity_helper import EntityHelper
from masyg_extractor.integrations.accounting.quickbooks.base_adapter import IntegrationClientAdapter
from masyg_extractor.integrations.accounting.quickbooks.services.audit_log_service import (
    AuditLogService,
    audit_op,  # used by subclasses, not here
)
from masyg_extractor.integrations.accounting.quickbooks.services.customer_service import CustomerService
from masyg_extractor.integrations.accounting.quickbooks.services.item_service import ItemService
from masyg_extractor.integrations.accounting.shared.firestore_repository import QuickBooksFirestoreService
from masyg_extractor.integrations.accounting.shared.identifiers import safe_uuid_key
from masyg_extractor.integrations.accounting.shared.operation_progress import AccountingOperationProgress
from masyg_extractor.integrations.accounting.shared.provider_batching import (
    chunk_accounting_provider_documents,
)
from masyg_extractor.integrations.utils import format_date
from masyg_extractor.integrations.accounting.shared.sku import generate_sku
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


def _safe_provider_text(value: Any, *, limit: int = 320) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _quickbooks_fault_message(payload: Dict[str, Any]) -> str:
    """Extract safe QuickBooks validation text from a batch Fault."""
    fault = payload.get("Fault") if isinstance(payload, dict) else None
    errors = fault.get("Error") if isinstance(fault, dict) else None
    first = errors[0] if isinstance(errors, list) and errors else None

    if not isinstance(first, dict):
        return "QuickBooks rejected this document."

    message = _safe_provider_text(first.get("Message"))
    detail = _safe_provider_text(first.get("Detail"))
    code = _safe_provider_text(first.get("code"), limit=40)

    parts = []
    if message:
        parts.append(message)
    if detail and detail.lower() != message.lower():
        parts.append(detail)

    text = ": ".join(parts) if parts else "QuickBooks rejected this document."
    if code:
        text = f"{text} (QuickBooks code {code})"
    return text


def _quickbooks_transport_message(payload: Dict[str, Any]) -> str:
    raw = _safe_provider_text(payload.get("error") if isinstance(payload, dict) else "")
    lowered = raw.lower()

    if any(token in lowered for token in ("401", "unauthor", "token", "auth")):
        return "QuickBooks authorization was rejected. Reconnect QuickBooks and try again."
    if any(token in lowered for token in ("429", "rate", "limit")):
        return "QuickBooks rate limit was reached. Wait a moment and try again."
    if "timeout" in lowered:
        return "QuickBooks did not respond in time. Please try again."
    return "QuickBooks could not complete the request. Please try again."


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
          - send to QB in provider-safe document chunks
          - per-item audit events (lean), batch envelope audit, and store successes
        """
        operation_progress = AccountingOperationProgress.from_context(
            self.context,
            provider="quickbooks",
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

        claimed_invoice_records: Dict[str, Dict[str, Any]] = {}
        settled_bids: set[str] = set()
        provider_started_bids: set[str] = set()

        try:
            document_payload_bulk: List[Dict[str, Any]] = []
            invoice_records: Dict[str, Dict[str, Any]] = {}
            customers_map: Dict[str, Customer] = {}
            items_map: Dict[str, List[Item]] = {}
            existing_documents = 0

            for document in documents:
                if not document.group_id or not document.group_id.strip():
                    await self._log("Group ID is required for invoice creation.", "error")
                    await operation_progress.failed(
                        document.transaction_id,
                        filename=get_original_filename(document.transaction_id),
                        error="Group ID is required.",
                    )
                    continue

                if await self._record_exists(document.group_id, document.transaction_id):
                    dup_msg = (
                        f"{self.doc_type} for ({get_original_filename(document.transaction_id)}) "
                        f"was already sent to {self.repo.integration} and was not sent again."
                    )
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
                return operation_progress.result_payload()

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
                        await operation_progress.failed(
                            document.transaction_id,
                            filename=get_original_filename(document.transaction_id),
                            error="Items could not be prepared.",
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
                        await operation_progress.failed(
                            document.transaction_id,
                            filename=get_original_filename(document.transaction_id),
                            error="No valid line items could be prepared.",
                        )
                        continue

                    valid_customer = customers_created.get(key) or None
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
                    await operation_progress.update(
                        document.transaction_id,
                        status="running",
                        progress=65,
                        filename=get_original_filename(document.transaction_id),
                        message="Ready to send to QuickBooks",
                    )

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
                    await operation_progress.failed(
                        document.transaction_id,
                        filename=get_original_filename(document.transaction_id),
                        error=str(e),
                    )
                    await self._log(
                        f"❌ Failed to process invoice for file {document.transaction_id}: {str(e)}", "error"
                    )

            record_type = f"{self.doc_type.lower()}s"
            claimed_payloads: List[Dict[str, Any]] = []

            for candidate_payload in document_payload_bulk:
                bid = candidate_payload.get("bId")
                inv = invoice_records.get(bid)

                if not bid or not inv:
                    continue

                action = (
                    "create_sales_receipt"
                    if self.doc_type.lower() == "salesreceipt"
                    else "create_ar_invoice"
                )

                claimed = await asyncio.to_thread(
                    self.repo.claim_record,
                    record_type,
                    inv["group_id"],
                    inv["transactionId"],
                    {
                        **inv,
                        "action": action,
                    },
                )

                if not claimed:
                    dup_msg = (
                        f"{self.doc_type} for "
                        f"({get_original_filename(inv['transactionId'])}) "
                        f"was already claimed or sent to "
                        f"{self.repo.integration} and was not sent again."
                    )
                    await self._log(f"❌ {dup_msg}", "error")
                    await operation_progress.failed(
                        inv["transactionId"],
                        filename=get_original_filename(
                            inv["transactionId"]
                        ),
                        error=dup_msg,
                    )
                    continue

                claimed_payloads.append(candidate_payload)
                claimed_invoice_records[bid] = inv

            document_payload_bulk = claimed_payloads
            invoice_records = claimed_invoice_records

            if not document_payload_bulk:
                await operation_progress.fail_remaining("No valid documents were processed.")
                await operation_progress.fail_remaining(
                    "No valid QuickBooks document could be prepared from the submitted data."
                )
                return operation_progress.result_payload()

            document_chunks = (
                chunk_accounting_provider_documents(
                    "quickbooks",
                    document_payload_bulk,
                )
            )

            for (
                chunk_index,
                document_chunk,
            ) in enumerate(
                document_chunks,
                start=1,
            ):
                chunk_bids = [
                    payload.get("bId")
                    for payload in document_chunk
                    if payload.get("bId")
                ]

                chunk_invoice_records = {
                    bid: invoice_records[bid]
                    for bid in chunk_bids
                    if bid in invoice_records
                }

                if not chunk_invoice_records:
                    continue

                first_transaction_id = next(
                    iter(
                        chunk_invoice_records.values()
                    )
                )["transactionId"]

                batch_event_id = (
                    f"{self.doc_type}:Batch:"
                    f"{first_transaction_id}:"
                    f"{chunk_index}:"
                    f"{len(document_chunk)}"
                )

                self.audit.start(
                    event_id=batch_event_id,
                    doc_type=self.doc_type,
                    entity_type=self.doc_type,
                    operation="batch_create",
                    transaction_id=None,
                    group_id=None,
                    idempotency_key=None,
                    payload={
                        "BatchItemRequest": [
                            payload.get(
                                self.doc_type,
                                {},
                            )
                            for payload
                            in document_chunk
                        ]
                    },
                )

                # Audit only the documents in the provider request
                # that is about to start. A later unsent chunk must
                # not look like a provider attempt.
                for (
                    bid,
                    inv,
                ) in chunk_invoice_records.items():
                    item_event_id = (
                        f"{self.doc_type}:"
                        f"{self.doc_type}:"
                        f"{inv['transactionId']}:"
                        f"{bid}"
                    )

                    self.audit.start(
                        event_id=item_event_id,
                        doc_type=self.doc_type,
                        entity_type=self.doc_type,
                        operation="create",
                        transaction_id=(
                            inv["transactionId"]
                        ),
                        group_id=inv["group_id"],
                        idempotency_key=bid,
                        payload=None,
                    )

                bulk_payload = {
                    "BatchItemRequest":
                        document_chunk
                }

                # Resolve local credentials before a provider attempt
                # is recorded. If token retrieval fails, no request
                # reached QuickBooks and these claims remain releasable.
                quickbooks_token = (
                    self.repo.get_integration_token()
                )

                # From this point forward the provider outcome may be
                # ambiguous for exactly these documents.
                provider_started_bids.update(
                    chunk_invoice_records.keys()
                )

                quickbooks_response = (
                    await self.client.request(
                        quickbooks_token=(
                            quickbooks_token
                        ),
                        payload=bulk_payload,
                        endpoint="batch",
                        method="POST",
                    )
                )

                if quickbooks_response.get(
                    "error"
                ):
                    provider_error = (
                        _quickbooks_transport_message(
                            quickbooks_response
                        )
                    )

                    # This chunk reached the provider boundary.
                    # Preserve every claim as uncertain.
                    for (
                        bid,
                        inv,
                    ) in (
                        chunk_invoice_records.items()
                    ):
                        await asyncio.to_thread(
                            self.repo.mark_record_uncertain,
                            record_type,
                            inv["group_id"],
                            inv["transactionId"],
                            error=provider_error,
                        )
                        settled_bids.add(bid)

                    # Later chunks have not reached QuickBooks.
                    # Release those claims so a safe retry remains
                    # possible rather than incorrectly blocking them
                    # as uncertain.
                    for (
                        pending_bid,
                        pending_inv,
                    ) in (
                        claimed_invoice_records.items()
                    ):
                        if (
                            pending_bid
                            in settled_bids
                            or pending_bid
                            in provider_started_bids
                        ):
                            continue

                        await asyncio.to_thread(
                            self.repo.release_record_claim,
                            record_type,
                            pending_inv["group_id"],
                            pending_inv[
                                "transactionId"
                            ],
                        )

                        settled_bids.add(
                            pending_bid
                        )

                        await operation_progress.failed(
                            pending_inv[
                                "transactionId"
                            ],
                            filename=(
                                get_original_filename(
                                    pending_inv[
                                        "transactionId"
                                    ]
                                )
                            ),
                            error=(
                                "QuickBooks batch stopped "
                                "before this document was "
                                "sent."
                            ),
                        )

                    await operation_progress.fail_remaining(
                        provider_error
                    )

                    await sio.emit(
                        "quickbooks-invoice-progress",
                        {"progress": 100},
                        room=self.context.client_id,
                    )

                    return (
                        operation_progress
                        .result_payload()
                    )

                response_payload = (
                    quickbooks_response.get(
                        "BatchItemResponse",
                        [],
                    )
                    or []
                )

                responded_bids: set[str] = set()

                for payload in response_payload:
                    bid = payload.get("bId")

                    inv = (
                        chunk_invoice_records
                        .get(bid)
                    )

                    if not inv:
                        continue

                    responded_bids.add(bid)

                    item_event_id = (
                        f"{self.doc_type}:"
                        f"{self.doc_type}:"
                        f"{inv['transactionId']}:"
                        f"{bid}"
                    )

                    file = get_original_filename(
                        inv.get("transactionId")
                    )

                    if "Fault" in payload:
                        self.audit.fail(
                            event_id=item_event_id,
                            group_id=(
                                inv["group_id"]
                            ),
                            transaction_id=(
                                inv[
                                    "transactionId"
                                ]
                            ),
                            error_category=(
                                "Validation"
                            ),
                            error_message=(
                                "QuickBooks returned "
                                "Fault"
                            ),
                            error_details=None,
                            retryable=(
                                _is_retryable(
                                    payload
                                )
                            ),
                        )

                        await operation_progress.failed(
                            inv[
                                "transactionId"
                            ],
                            filename=file,
                            error=(
                                _quickbooks_fault_message(
                                    payload
                                )
                            ),
                        )

                        await asyncio.to_thread(
                            self.repo.release_record_claim,
                            record_type,
                            inv["group_id"],
                            inv[
                                "transactionId"
                            ],
                        )

                        settled_bids.add(bid)

                        await self._log(
                            f"❌ Failed to create "
                            f"{inv.get('transactionType')} "
                            f"- document: {file}",
                            "error",
                        )

                    else:
                        self.audit.ok(
                            event_id=item_event_id,
                            group_id=(
                                inv["group_id"]
                            ),
                            transaction_id=(
                                inv[
                                    "transactionId"
                                ]
                            ),
                        )

                        await operation_progress.succeeded(
                            inv[
                                "transactionId"
                            ],
                            filename=file,
                            message=(
                                "Created in QuickBooks"
                            ),
                        )

                        await self._log(
                            f"✅ "
                            f"{self.doc_type.capitalize()} "
                            f"processed successfully "
                            f"- document: {file}"
                        )

                        provider_entity = (
                            payload.get(
                                self.doc_type
                            )
                        )

                        provider_document_id = (
                            provider_entity.get(
                                "Id"
                            )
                            if isinstance(
                                provider_entity,
                                dict,
                            )
                            else None
                        )

                        final_record = dict(inv)

                        if provider_document_id:
                            final_record[
                                "providerDocumentId"
                            ] = str(
                                provider_document_id
                            )

                        await asyncio.to_thread(
                            self.repo.finalize_record,
                            record_type,
                            inv["group_id"],
                            inv[
                                "transactionId"
                            ],
                            final_record,
                        )

                        settled_bids.add(bid)

                # Missing result from a provider-started chunk is
                # ambiguous. Preserve its duplicate barrier.
                for (
                    bid,
                    inv,
                ) in (
                    chunk_invoice_records.items()
                ):
                    if bid in responded_bids:
                        continue

                    await asyncio.to_thread(
                        self.repo.mark_record_uncertain,
                        record_type,
                        inv["group_id"],
                        inv["transactionId"],
                        error=(
                            "QuickBooks did not "
                            "return a result for "
                            "this document."
                        ),
                    )

                    settled_bids.add(bid)

                any_success = any(
                    "Fault" not in payload
                    for payload
                    in response_payload
                )

                if any_success:
                    self.audit.ok(
                        event_id=batch_event_id,
                        group_id=None,
                        transaction_id=None,
                    )
                else:
                    self.audit.fail(
                        event_id=batch_event_id,
                        group_id=None,
                        transaction_id=None,
                        error_category="Unknown",
                        error_message=(
                            "All items failed"
                        ),
                        error_details=(
                            quickbooks_response
                        ),
                        retryable=True,
                    )

            await operation_progress.fail_remaining(
                "QuickBooks did not return a "
                "result for this document."
            )

            await sio.emit(
                "quickbooks-invoice-progress",
                {"progress": 100},
                room=self.context.client_id,
            )

            return (
                operation_progress.result_payload()
            )

        except Exception as e:
            claim_error = str(e).strip() or (
                "QuickBooks operation failed unexpectedly."
            )

            for bid, inv in claimed_invoice_records.items():
                if bid in settled_bids:
                    continue

                try:
                    if bid in provider_started_bids:
                        await asyncio.to_thread(
                            self.repo.mark_record_uncertain,
                            f"{self.doc_type.lower()}s",
                            inv["group_id"],
                            inv["transactionId"],
                            error=claim_error,
                        )
                    else:
                        await asyncio.to_thread(
                            self.repo.release_record_claim,
                            f"{self.doc_type.lower()}s",
                            inv["group_id"],
                            inv["transactionId"],
                        )
                except Exception as cleanup_error:
                    logger.error(
                        "Failed to settle QuickBooks accounting claim "
                        "transaction=%s error=%s",
                        inv.get("transactionId"),
                        cleanup_error,
                    )

            error_msg = (
                f"❌ Error in bulk sending of {self.doc_type} "
                f"documents: {str(e)}"
            )
            await operation_progress.fail_remaining(str(e))
            await sio.emit("quickbooks-invoice-progress", {"progress": 100}, room=self.context.client_id)
            await self._log(error_msg, "error")
            return operation_progress.result_payload()

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
