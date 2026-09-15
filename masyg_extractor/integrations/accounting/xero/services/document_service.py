import asyncio
import uuid
from pprint import pprint
from typing import List, Dict, Any

from masyg_extractor.integrations.accounting.shared.identifiers import safe_uuid_key
from masyg_extractor.integrations.accounting.shared.operation_progress import AccountingOperationProgress
from masyg_extractor.integrations.accounting.shared.provider_batching import (
    chunk_accounting_provider_documents,
)
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

def _xero_accounting_record_type(
    invoice_status: str,
) -> str:
    return (
        "bills"
        if str(invoice_status or "").upper() == "ACCPAY"
        else "invoices"
    )


def _xero_legacy_accounting_record_type(
    doc_type: str,
) -> str:
    # Preserve the historical storage formula for compatibility.
    # Active bulk routes use doc_type="Invoices", which historically
    # produced the Firestore collection name "invoicess".
    return f"{str(doc_type or '').lower()}s"


def _xero_provider_error_is_ambiguous(
    payload: Dict[str, Any],
) -> bool:
    """
    Provider/network failures at 5xx or without a reliable HTTP status
    may have happened after Xero accepted the create request.

    Keep the claim in those cases rather than risking a duplicate.
    """
    try:
        status_code = int(payload.get("status_code"))
    except (TypeError, ValueError):
        return True

    return status_code >= 500


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
          - Sends invoices in provider-safe request chunks.
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

        record_type = _xero_accounting_record_type(
            invoice_status
        )
        legacy_record_type = (
            _xero_legacy_accounting_record_type(
                self.doc_type
            )
        )
        action = (
            "create_ap_bill"
            if record_type == "bills"
            else "create_ar_invoice"
        )

        claimed_records: Dict[str, Dict[str, Any]] = {}
        claim_tokens_by_transaction_id: Dict[str, str] = {}
        settled_transaction_ids: set[str] = set()
        provider_started_transaction_ids: set[str] = set()

        try:
            document_payload_bulk = []
            invoice_records = []
            prepared_documents = []
            claimed_documents = []
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

                canonical_exists = await asyncio.to_thread(
                    self.repo.record_exists,
                    record_type,
                    document.group_id,
                    document.transaction_id,
                )

                legacy_exists = False
                if legacy_record_type != record_type:
                    legacy_exists = await asyncio.to_thread(
                        self.repo.record_exists,
                        legacy_record_type,
                        document.group_id,
                        document.transaction_id,
                    )

                if canonical_exists or legacy_exists:
                    duplicate_location = (
                        legacy_record_type
                        if legacy_exists
                        else record_type
                    )
                    dup_msg = (
                        f"{self.doc_type} for "
                        f"({get_original_filename(document.transaction_id)}) "
                        f"already recorded in Xero "
                        f"({duplicate_location})."
                    )
                    await self._log(
                        f"❌ {dup_msg}",
                        "error",
                    )
                    await operation_progress.failed(
                        document.transaction_id,
                        filename=get_original_filename(
                            document.transaction_id
                        ),
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

                provisional_record = {
                    "group_id": document.group_id,
                    "transactionId":
                        document.transaction_id,
                    "integration": "xero",
                    "transactionType": self.doc_type,
                    "date": document.date,
                    "amount": sum(
                        float(item.quantity or 0)
                        * float(item.unit_price or 0)
                        for item in document.items
                    ),
                    "metadata": {
                        "syncToken": "0"
                    },
                    "action": action,
                    "invoiceStatus": invoice_status,
                }

                claim_token = uuid.uuid4().hex

                claimed = await asyncio.to_thread(
                    self.repo.claim_record,
                    record_type,
                    document.group_id,
                    document.transaction_id,
                    provisional_record,

                    claim_token=claim_token,)

                if not claimed:
                    dup_msg = (
                        f"{self.doc_type} for "
                        f"({get_original_filename(document.transaction_id)}) "
                        f"was already claimed or sent to Xero and "
                        f"was not sent again."
                    )

                    await operation_progress.failed(
                        document.transaction_id,
                        filename=get_original_filename(
                            document.transaction_id
                        ),
                        error=dup_msg,
                    )

                    await self._log(
                        f"❌ {dup_msg}",
                        "error",
                    )

                    continue

                claim_tokens_by_transaction_id[
                    document.transaction_id
                ] = claim_token

                claimed_records[
                    document.transaction_id
                ] = provisional_record

                claimed_documents.append(
                    document
                )

                key = safe_uuid_key(
                    document.transaction_id
                )

                # Only documents that own the durable claim may
                # participate in provider-side Contact/Item work.
                customers_map[key] = document.customer
                items_map[key] = document.items


            if not claimed_documents:
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

            async def release_preparation_claim(
                document: Document,
            ) -> None:
                transaction_id = (
                    document.transaction_id
                )

                if (
                    transaction_id
                    in settled_transaction_ids
                    or transaction_id
                    not in claimed_records
                ):
                    return

                await asyncio.to_thread(
                    self.repo.release_record_claim,
                    record_type,
                    document.group_id,
                    transaction_id,

                    claim_token=claim_tokens_by_transaction_id[transaction_id],)

                settled_transaction_ids.add(
                    transaction_id
                )

            # Build payloads only for documents that own the
            # durable accounting claim acquired above.
            for document in claimed_documents:
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

                        await release_preparation_claim(
                            document
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

                        await release_preparation_claim(
                            document
                        )

                        continue
                    valid_customer_id = valid_customer.id
                    doc_number = generate_doc_number(self.doc_number_prefix)

                    dispatch_identity_prepared = (
                        await asyncio.to_thread(
                            self.repo.prepare_provider_dispatch,
                            record_type,
                            document.group_id,
                            document.transaction_id,
                            provider_document_number=doc_number,

                            claim_token=claim_tokens_by_transaction_id[document.transaction_id],)
                    )

                    if not dispatch_identity_prepared:
                        raise RuntimeError(
                            "Xero durable dispatch identity "
                            "could not be persisted."
                        )

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
                    invoice_records.append(
                        invoice_record
                    )

                    claimed_records[
                        document.transaction_id
                    ] = invoice_record

                except Exception as e:
                    await operation_progress.failed(
                        document.transaction_id,
                        filename=get_original_filename(document.transaction_id),
                        error=str(e),
                    )

                    await release_preparation_claim(
                        document
                    )

                    await self._log(
                        f"❌ Failed to process invoice for file {document.transaction_id}: {str(e)}",
                        "error",
                    )

            if document_payload_bulk:
                # Every prepared document already owns its durable
                # accounting claim. No second claim is permitted here.

                document_chunks = (
                    chunk_accounting_provider_documents(
                        "xero",
                        document_payload_bulk,
                    )
                )

                chunk_offset = 0

                for document_chunk in document_chunks:
                    chunk_size = len(document_chunk)

                    chunk_documents = (
                        prepared_documents[
                            chunk_offset:
                            chunk_offset + chunk_size
                        ]
                    )

                    chunk_invoice_records = (
                        invoice_records[
                            chunk_offset:
                            chunk_offset + chunk_size
                        ]
                    )

                    chunk_offset += chunk_size

                    if not (
                        len(document_chunk)
                        == len(chunk_documents)
                        == len(chunk_invoice_records)
                    ):
                        raise RuntimeError(
                            "Xero provider chunk alignment "
                            "was lost."
                        )

                    bulk_payload = {
                        "Invoices": document_chunk
                    }

                    # Resolve local credentials before marking this
                    # chunk as provider-started. If token lookup fails,
                    # none of these documents reached Xero.
                    xero_token = (
                        self.repo.get_integration_token()
                    )

                    chunk_transaction_ids = {
                        document.transaction_id
                        for document in chunk_documents
                    }

                    # From this boundary forward, only documents in
                    # this chunk have an ambiguous provider outcome if
                    # transport/execution fails.
                    for dispatch_record in chunk_invoice_records:
                        dispatch_started = (
                            await asyncio.to_thread(
                                self.repo.mark_provider_dispatch_started,
                                record_type,
                                dispatch_record["group_id"],
                                dispatch_record["transactionId"],

                                claim_token=claim_tokens_by_transaction_id[dispatch_record["transactionId"]],)
                        )

                        if not dispatch_started:
                            raise RuntimeError(
                                "Xero durable provider dispatch "
                                "marker could not be persisted."
                            )

                    provider_started_transaction_ids.update(
                        chunk_transaction_ids
                    )

                    xero_response = await self.client.request(
                        xero_token=xero_token,
                        payload=bulk_payload,
                        endpoint="Invoices",
                        method="POST"
                    )

                    provider_error_by_index: dict[
                        int,
                        str,
                    ] = {}

                    raw_document_errors = (
                        xero_response.get(
                            "document_errors"
                        )
                    )

                    if isinstance(
                        raw_document_errors,
                        list,
                    ):
                        for entry in raw_document_errors:
                            if not isinstance(
                                entry,
                                dict,
                            ):
                                continue

                            try:
                                index = int(
                                    entry.get("index")
                                )
                            except (
                                TypeError,
                                ValueError,
                            ):
                                continue

                            message = str(
                                entry.get("message")
                                or ""
                            ).strip()

                            if message:
                                provider_error_by_index[
                                    index
                                ] = message

                    response_invoices = (
                        xero_response.get(
                            "Invoices"
                        )
                    )

                    if not isinstance(
                        response_invoices,
                        list,
                    ):
                        response_invoices = []

                    if "error" in xero_response:
                        default_error = str(
                            xero_response.get("error")
                            or (
                                "Xero rejected this "
                                "document."
                            )
                        ).strip()

                        ambiguous_default = (
                            _xero_provider_error_is_ambiguous(
                                xero_response
                            )
                        )

                        for (
                            index,
                            document,
                        ) in enumerate(
                            chunk_documents
                        ):
                            error_message = (
                                provider_error_by_index.get(
                                    index,
                                    default_error
                                    or (
                                        "Xero rejected this "
                                        "document."
                                    ),
                                )
                            )

                            await operation_progress.failed(
                                document.transaction_id,
                                filename=(
                                    get_original_filename(
                                        document.transaction_id
                                    )
                                ),
                                error=error_message,
                            )

                            explicit_provider_rejection = (
                                index
                                in provider_error_by_index
                            )

                            if (
                                ambiguous_default
                                and not
                                explicit_provider_rejection
                            ):
                                await asyncio.to_thread(
                                    self.repo.mark_record_uncertain,
                                    record_type,
                                    document.group_id,
                                    document.transaction_id,
                                    error=error_message,

                                    claim_token=claim_tokens_by_transaction_id[document.transaction_id],)
                            else:
                                await asyncio.to_thread(
                                    self.repo.release_record_claim,
                                    record_type,
                                    document.group_id,
                                    document.transaction_id,

                                    claim_token=claim_tokens_by_transaction_id[document.transaction_id],)

                            settled_transaction_ids.add(
                                document.transaction_id
                            )

                            await self._log(
                                f"❌ Failed to create "
                                f"{self.doc_type} "
                                f"{get_original_filename(document.transaction_id)} "
                                f"in Xero: {error_message}",
                                "error",
                            )

                        # A top-level Xero error stops this execution.
                        # Claims belonging to later chunks have not
                        # crossed the provider boundary and must remain
                        # safely retryable.
                        for (
                            pending_transaction_id,
                            pending_invoice_record,
                        ) in claimed_records.items():
                            if (
                                pending_transaction_id
                                in settled_transaction_ids
                                or pending_transaction_id
                                in provider_started_transaction_ids
                            ):
                                continue

                            pending_group_id = (
                                pending_invoice_record.get(
                                    "group_id"
                                )
                            )

                            await asyncio.to_thread(
                                self.repo.release_record_claim,
                                record_type,
                                pending_group_id,
                                pending_transaction_id,

                                claim_token=claim_tokens_by_transaction_id[pending_transaction_id],)

                            settled_transaction_ids.add(
                                pending_transaction_id
                            )

                            await operation_progress.failed(
                                pending_transaction_id,
                                filename=(
                                    get_original_filename(
                                        pending_transaction_id
                                    )
                                ),
                                error=(
                                    "Xero batch stopped "
                                    "before this document "
                                    "was sent."
                                ),
                            )

                        await operation_progress.fail_remaining(
                            default_error
                        )

                        await sio.emit(
                            "xero-invoice-progress",
                            {"progress": 100},
                            room=self.context.client_id,
                        )

                        return (
                            operation_progress
                            .result_payload()
                        )

                    for (
                        index,
                        document,
                    ) in enumerate(
                        chunk_documents
                    ):
                        provider_invoice = (
                            response_invoices[index]
                            if index
                            < len(response_invoices)
                            else None
                        )

                        # Missing result from this provider-started
                        # chunk is ambiguous and is resolved below.
                        if provider_invoice is None:
                            continue

                        provider_error = (
                            _xero_response_invoice_error(
                                provider_invoice
                            )
                        )

                        if provider_error:
                            await operation_progress.failed(
                                document.transaction_id,
                                filename=(
                                    get_original_filename(
                                        document.transaction_id
                                    )
                                ),
                                error=provider_error,
                            )

                            await asyncio.to_thread(
                                self.repo.release_record_claim,
                                record_type,
                                document.group_id,
                                document.transaction_id,

                                claim_token=claim_tokens_by_transaction_id[document.transaction_id],)

                            settled_transaction_ids.add(
                                document.transaction_id
                            )

                            await self._log(
                                f"❌ Xero rejected "
                                f"{self.doc_type} "
                                f"{get_original_filename(document.transaction_id)}: "
                                f"{provider_error}",
                                "error",
                            )

                            continue

                        invoice_record = (
                            chunk_invoice_records[index]
                            if index
                            < len(
                                chunk_invoice_records
                            )
                            else {
                                "group_id":
                                    document.group_id,
                                "transactionId":
                                    document.transaction_id,
                                "integration": "xero",
                                "transactionType":
                                    self.doc_type,
                            }
                        )

                        provider_document_id = (
                            provider_invoice.get(
                                "InvoiceID"
                            )
                            if isinstance(
                                provider_invoice,
                                dict,
                            )
                            else None
                        )

                        final_record = dict(
                            invoice_record
                        )

                        if provider_document_id:
                            final_record[
                                "providerDocumentId"
                            ] = str(
                                provider_document_id
                            )

                        provider_document_number = (
                            provider_invoice.get(
                                "InvoiceNumber"
                            )
                            if isinstance(
                                provider_invoice,
                                dict,
                            )
                            else None
                        )

                        if provider_document_number:
                            final_record[
                                "providerDocumentNumber"
                            ] = str(
                                provider_document_number
                            )

                        finalized = await asyncio.to_thread(
                                                    self.repo.finalize_record,
                                                    record_type,
                                                    document.group_id,
                                                    document.transaction_id,
                                                    final_record,

                                                    claim_token=claim_tokens_by_transaction_id[document.transaction_id],)

                        if not finalized:
                            raise RuntimeError(
                                "Xero durable success "
                                "finalization lost claim ownership."
                            )

                        settled_transaction_ids.add(
                            document.transaction_id
                        )

                        await operation_progress.succeeded(
                            document.transaction_id,
                            filename=(
                                get_original_filename(
                                    document.transaction_id
                                )
                            ),
                            message="Created in Xero",
                        )

                        await self._log(
                            f"✅ "
                            f"{self.doc_type.capitalize()} "
                            f"created in Xero for "
                            f"{document.customer.name} "
                            f"({get_original_filename(document.transaction_id)})"
                        )

                    # Resolve only missing results from this exact
                    # provider-started chunk. Later unsent chunks must
                    # never become uncertain because an earlier Xero
                    # request omitted a result.
                    for document in chunk_documents:
                        if (
                            document.transaction_id
                            in settled_transaction_ids
                        ):
                            continue

                        missing_result_error = (
                            "Xero did not return a result "
                            "for this document."
                        )

                        await asyncio.to_thread(
                            self.repo.mark_record_uncertain,
                            record_type,
                            document.group_id,
                            document.transaction_id,
                            error=missing_result_error,

                            claim_token=claim_tokens_by_transaction_id[document.transaction_id],)

                        settled_transaction_ids.add(
                            document.transaction_id
                        )

                        await operation_progress.failed(
                            document.transaction_id,
                            filename=(
                                get_original_filename(
                                    document.transaction_id
                                )
                            ),
                            error=missing_result_error,
                        )

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
            error_message = (
                str(e).strip()
                or "Xero operation failed."
            )

            for (
                transaction_id,
                invoice_record,
            ) in claimed_records.items():
                if (
                    transaction_id
                    in settled_transaction_ids
                ):
                    continue

                group_id = invoice_record.get(
                    "group_id"
                )

                try:
                    if (
                        transaction_id
                        in provider_started_transaction_ids
                    ):
                        await asyncio.to_thread(
                            self.repo.mark_record_uncertain,
                            record_type,
                            group_id,
                            transaction_id,
                            error=error_message,

                            claim_token=claim_tokens_by_transaction_id[transaction_id],)
                    else:
                        await asyncio.to_thread(
                            self.repo.release_record_claim,
                            record_type,
                            group_id,
                            transaction_id,

                            claim_token=claim_tokens_by_transaction_id[transaction_id],)
                except Exception as cleanup_error:
                    logger.error(
                        "Failed to settle Xero accounting claim "
                        "transaction=%s error=%s",
                        transaction_id,
                        cleanup_error,
                    )

            await operation_progress.fail_remaining(
                error_message
            )
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
