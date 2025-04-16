import asyncio
from typing import List, Dict, Any, Optional
from fastapi import Request

from masyg_extractor.integration_v2.core.integration_context import IntegrationContext
from masyg_extractor.integration_v2.domain.models import Item, Customer, Invoice, Document
from masyg_extractor.integration_v2.entity_helper import EntityHelper
from masyg_extractor.integration_v2.intergrate.baseAdapter import IntegrationClientAdapter
from masyg_extractor.integration_v2.intergrate.quickbooks.services.item_service import ItemService
from masyg_extractor.integration_v2.repository.firestore_repository import QuickBooksFirestoreService
from masyg_extractor.services.log_manager import LogManager
from masyg_extractor.services.my_log import logger, send_log
from masyg_extractor.integrations.quickbooks.quickbooks_client import quickbooks_request
from masyg_extractor.integrations.quickbooks.repository.firestore_repository import (
    store_invoice_record,
    invoice_exists_in_firestore
)
from masyg_extractor.integration_v2.intergrate.quickbooks.services.customer_service import CustomerService

from masyg_extractor.integrations.quickbooks.services.item_service import check_item_exists, create_item
from masyg_extractor.integrations.transaction_helpers import generate_doc_number, check_duplicate_record
from masyg_extractor.services.progress_log import IntegrationsProgressLog
from masyg_extractor.utils.tool import get_original_filename


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

    async def send_document(
            self,

            document: Document,
            # items: List[Item],
            # transaction_id: str,

            # customer: Customer,
            share_progress: float

    ) -> Dict[str, Any] or str:
        """
        Asynchronously creates an invoice in QuickBooks and stores key invoice info in Firestore.
        """
        log_manager = LogManager()
        await log_manager.clear_queue()
        try:
            steps = 5

            for step in range(steps):
                await asyncio.sleep(0.3)
                self.context.progress[f"creating_{self.doc_type}"] = ((
                                                                              step + 1) / steps) * IntegrationsProgressLog.CREATING_ITEM_WEIGHT
                await self.context.progress_logger.safe_emit_progress(share_progress)

            if not document.group_id or document.group_id.strip() == "":
                return {"error": "Group ID is required for invoice creation."}

            # Offload duplicate check to a worker thread.
            dup = await asyncio.to_thread(
                self.repo.record_exists,
                self.doc_type.lower()+"s",

                document.group_id,
                document.transaction_id,

            )


            if dup:

                msg = f"{self.doc_type} for ({get_original_filename(document.transaction_id)}) already recorded in QuickBooks."
                # Log asynchronously without blocking the current execution.

                await asyncio.sleep(1)
                await self.context.log_manager.send_log(f"❌ {msg}", log_key=f"invoice-log-message".strip(), user_room=self.context.client_id)
                await asyncio.sleep(1)
                # return dup
                raise Exception(msg)

            # Offload customer lookup/creation.
            valid_customer_id = await self.customer_service.get_or_create_customer(
                document.customer
            )
            # logger.info(f"Using customer ID: {valid_customer_id}")

            if not document.items:
                logger.info("No items provided for invoice.")
                return {"error": "Items required for invoice creation."}

            line_items = []
            total_amount = 0.0
            for idx, item in enumerate(document.items):
                # print("0k0k0k0k0k")
                item_name = item.name
                item_id = item.id
                exists = await self.item_service.check_item_exists(
                    item
                )
                if not exists:
                    new_id = await self.item_service.create_item(item)
                    if not new_id:
                        logger.error("Failed to create item: Received invalid item ID")
                        # Handle error appropriately.
                    else:
                        item.id = new_id
                        item_id = new_id


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
                        # "ItemRef": {"value": str(item_id)},
                        "Qty": int(quantity),
                        "UnitPrice": float(unit_price),
                        "TaxCodeRef": {"value": tax_code}
                    }
                })

            doc_number = generate_doc_number(self.doc_number_prefix)
            date = document.date
            customer_name = document.customer.name
            payload = {
                "CustomerRef": {"value": valid_customer_id, "name": customer_name},
                "AutoDocNumber": False,
                # "EmailStatus": "NotSet",
                "Line": line_items,
                # "TotalAmt": total_amount,
                "TxnDate": date,
                "CurrencyRef": {"value": "USD"},
                # "PrintStatus": "NeedToPrint",
                "DocNumber": doc_number
            }
            logger.info(f"{self.doc_type} payload prepared for doc_number: {doc_number}")

            logger.info("About to call quickbooks_request")

            document_id = await self.entity_helper.create_entity(self.doc_type, payload)

            # # Offload the synchronous HTTP request.
            # response = await quickbooks_request(
            #     request,
            #     "invoice",user_id=user_id,
            #     payload=payload,
            #     method="POST",
            #     client_id=client_id)
            # logger.info(f"response from quickbook request: {response}")
            #
            # if isinstance(response, dict) and "fault" in response:
            #     error_msg = f"Unexpected response structure: {response}"
            #     logger.error(error_msg)
            #     return {"error": error_msg}
            # print("--0999999")
            if self.context.user_id:
                document_record = {
                    "integration": "quickbooks",
                    "transactionType": self.doc_type,
                    "transactionId": document.transaction_id,
                    "docNumber": doc_number,
                    "customerId": valid_customer_id,
                    "date": date,
                    "amount": total_amount,
                    "metadata": {"syncToken": "0"}
                }
                await asyncio.to_thread(
                    self.repo.store_record,
                    self.doc_type.lower()+"s",

                    document.group_id,
                    document.transaction_id,
                    document_record,

                )

            if document_id and int(document_id) > 0:

                await self.context.log_manager.send_log(
                    f"✅ {self.doc_type.capitalize()} sent and processed {get_original_filename(document.transaction_id)} for {customer_name} successfully",
                    log_key=f"invoice-log-message", user_room=self.context.client_id
                )
            return document_id

        except Exception as e:
            logger.error(f"Exception in send_invoice")
            print(str(e))
            await self.context.log_manager.send_log(f"❌ Failed to create invoice please try again with file {document.transaction_id}",
                           log_key=f"{self.doc_type.lower()}-log-message", user_room=self.context.client_id)

            return {"error": str(e)}
