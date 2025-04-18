import asyncio
from typing import List, Dict, Any, Optional
from fastapi import Request

from masyg_extractor.integration_qb_v5.core.integration_context import IntegrationContext
from masyg_extractor.integration_qb_v5.domain.models import Item, Customer, Invoice
from masyg_extractor.integration_qb_v5.entity_helper import EntityHelper
from masyg_extractor.integration_qb_v5.intergrate.baseAdapter import IntegrationClientAdapter
from masyg_extractor.integration_qb_v5.intergrate.quickbooks.services.document_service import DocumentService
from masyg_extractor.integration_qb_v5.intergrate.quickbooks.services.item_service import ItemService
from masyg_extractor.integration_qb_v5.repository.firestore_repository import QuickBooksFirestoreService
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
from masyg_extractor.utils.tool import get_original_filename


class InvoiceService(DocumentService):
    def __init__(self, context: IntegrationContext, repo: QuickBooksFirestoreService, client: IntegrationClientAdapter):
        super().__init__("INV", "Invoice", context, repo, client)
        self.context = context
        self.repo = repo
        self.client = client
        self.item_service = ItemService(context, repo, client)

        self.customer_service = CustomerService(context, repo, client)
        self.entity_helper = EntityHelper(context, repo, client)

    async def send_invoice(
            self,

            invoice: Invoice,
            # items: List[Item],
            # transaction_id: str,

            # customer: Customer,
            share_progress: float

    ) -> Dict[str, Any]:
        """
        Asynchronously creates an invoice in QuickBooks and stores key invoice info in Firestore.
        """
        return await super().send_document(invoice, share_progress)

    async def send_invoice_in_bulk(
            self,

            invoices: List[Invoice],
            # items: List[Item],
            # transaction_id: str,

            # customer: Customer,
            share_progress: float

    ) -> Dict[str, Any]:
        """
        Asynchronously creates an invoice in QuickBooks and stores key invoice info in Firestore.
        """
        return await super().send_document_in_bulk(invoices, share_progress)

        # try:
        #     steps = 5
        #
        #
        #     for step in range(steps):
        #         await asyncio.sleep(0.3)
        #         self.context.progress["creating_documents"] = ((
        #                                                                step + 1) / steps) * IntegrationsProgressLog.CREATING_ITEM_WEIGHT
        #         await self.context.progress_logger.safe_emit_progress(share_progress)
        #
        #     if not invoice.group_id or invoice.group_id.strip() == "":
        #         return {"error": "Group ID is required for invoice creation."}
        #
        #     # Offload duplicate check to a worker thread.
        #     dup = await asyncio.to_thread(
        #         self.repo.invoice_exists,
        #
        #         invoice.group_id,
        #         invoice.transaction_id,
        #
        #     )
        #     if dup:
        #         msg = f"Invoice for ({get_original_filename(invoice.transaction_id)}) already recorded in QuickBooks."
        #         # Log asynchronously without blocking the current execution.
        #         await send_log(f"❌ {msg}", log_key="qb-log-message", user_room=self.context.client_id)
        #         raise Exception(msg)
        #
        #     # Offload customer lookup/creation.
        #     valid_customer_id = await self.customer_service.get_or_create_customer(
        #         invoice.customer
        #     )
        #     # logger.info(f"Using customer ID: {valid_customer_id}")
        #
        #     if not invoice.items:
        #         logger.info("No items provided for invoice.")
        #         return {"error": "Items required for invoice creation."}
        #
        #     line_items = []
        #     total_amount = 0.0
        #     for idx, item in enumerate(invoice.items):
        #         # print("0k0k0k0k0k")
        #         item_name = item.name
        #         item_id = item.id
        #         exists = await self.item_service.check_item_exists(
        #             item
        #         )
        #         if not exists:
        #             logger.info(f"Item '{item_name}' not found; creating new item.")
        #             new_id = await self.item_service.create_item(
        #                 item,
        #
        #             )
        #             item.id = new_id
        #             item_id = new_id
        #
        #         quantity = item.quantity if item.quantity else 0
        #         unit_price = item.unit_price if item.unit_price else 0
        #         amount = float(quantity) * float(unit_price)
        #         # total_amount += amount
        #         tax_code = item.tax_code
        #         line_items.append({
        #             "DetailType": "SalesItemLineDetail",
        #             "Amount": amount,
        #             "Description": item.description if item.description else "",
        #             "SalesItemLineDetail": {
        #                 "ItemRef": {"value": str(item_id)},
        #                 "Qty": int(quantity),
        #                 "UnitPrice": float(unit_price),
        #                 "TaxCodeRef": {"value": tax_code}
        #             }
        #         })
        #
        #     doc_number = generate_doc_number("INV")
        #     date = invoice.date
        #     customer_name = invoice.customer.name
        #     payload = {
        #         "CustomerRef": {"value": valid_customer_id, "name": customer_name},
        #         "AutoDocNumber": False,
        #         # "EmailStatus": "NotSet",
        #         "Line": line_items,
        #         # "TotalAmt": total_amount,
        #         "TxnDate": date,
        #         "CurrencyRef": {"value": "USD"},
        #         # "PrintStatus": "NeedToPrint",
        #         "DocNumber": doc_number
        #     }
        #     logger.info(f"Invoice payload prepared for doc_number: {doc_number}")
        #
        #     logger.info("About to call quickbooks_request")
        #     print(payload)
        #     invoice_id = await self.entity_helper.create_entity("Invoice", payload)
        #     print(invoice_id)
        #     # # Offload the synchronous HTTP request.
        #     # response = await quickbooks_request(
        #     #     request,
        #     #     "invoice",user_id=user_id,
        #     #     payload=payload,
        #     #     method="POST",
        #     #     client_id=client_id)
        #     # logger.info(f"response from quickbook request: {response}")
        #     #
        #     # if isinstance(response, dict) and "fault" in response:
        #     #     error_msg = f"Unexpected response structure: {response}"
        #     #     logger.error(error_msg)
        #     #     return {"error": error_msg}
        #     # print("--0999999")
        #     if self.context.user_id:
        #         invoice_record = {
        #             "integration": "quickbooks",
        #             "transactionType": "Invoice",
        #             "transactionId": invoice.transaction_id,
        #             "docNumber": doc_number,
        #             "customerId": valid_customer_id,
        #             "date": date,
        #             "amount": total_amount,
        #             "metadata": {"syncToken": "0"}
        #         }
        #         await asyncio.to_thread(
        #             self.repo.store_invoice,
        #
        #             invoice.group_id,
        #             invoice.transaction_id,
        #             invoice_record,
        #
        #         )
        #
        #     if invoice_id and int(invoice_id) >0:
        #
        #             await send_log(
        #                 f"✅ Invoice sent and processed {get_original_filename(invoice.transaction_id)} for {customer_name} successfully",
        #              log_key = "qb-log-message", user_room=self.context.client_id
        #             )
        #     return invoice_id
        #
        # except Exception as e:
        #     logger.error(f"Exception in send_invoice")
        #     print(str(e))
        #     await send_log(f"❌ Failed to create invoice please try again with file {invoice.transaction_id}", log_key="qb-log-message", user_room=self.context.client_id)
        #
        #     return {"error": str(e)}
