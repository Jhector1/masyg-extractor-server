import asyncio
from typing import List, Dict, Any, Optional
from fastapi import Request

from masyg_extractor.integrations.utils import format_date
from masyg_extractor.services.my_log import logger, send_log
from masyg_extractor.integrations.xero.xero_client import xero_request
from masyg_extractor.integrations.xero.repository.firestore_repository import (
    store_invoice_record,
    invoice_exists_in_firestore
)
from masyg_extractor.integrations.xero.services.customer_services import get_or_create_customer
from masyg_extractor.integrations.xero.services.item_services import check_item_exists, create_item
from masyg_extractor.integrations.transaction_helpers import generate_doc_number, check_duplicate_record
from masyg_extractor.utils.tool import get_original_filename


class InvoiceService:
    @staticmethod
    async def send_invoice(
            request: Request,
            customer_name: str,
            customer_id: Optional[str],
            items: List[Dict[str, Any]],
            transaction_id: str,
            group_id: str,
            date: str,
            user_id: str,
            record_type: str = "invoices",
            client_id: str = ""
    ) -> Dict[str, Any]:
        """
        Asynchronously creates an invoice in Xero and stores key invoice info in Firestore.
        """
        try:
            if not group_id or group_id.strip() == "":
                return {"error": "Group ID is required for invoice creation."}

            # Offload duplicate check to a worker thread.
            dup = await asyncio.to_thread(
                check_duplicate_record,
                user_id,
                invoice_exists_in_firestore,
                record_type,
                group_id,
                transaction_id,
                client_id
            )
            if dup.get("error"):
                msg = f"{record_type.capitalize()} for ({get_original_filename(transaction_id)}) already recorded in Xero."
                await send_log(f"❌ {msg}",log_key="xero-log-message", user_room=client_id)
                await asyncio.sleep(1)
                return dup

            # Offload customer lookup/creation using Xero contacts.
            valid_customer_id = await get_or_create_customer(
                request,
                customer_id,
                customer_name,
                user_id,
                client_id=client_id
            )

            if not items:
                logger.info("No items provided for invoice.")
                return {"error": "Items required for invoice creation."}

            line_items = []
            total_amount = 0.0
            for idx, item in enumerate(items):
                item_name = item.get("item_name")
                item_id = item.get("item_id")
                exists = await check_item_exists(
                    request=request,
                    user_id=user_id,
                    item_name=item_name,
                    item_id=item_id,
                    client_id=client_id
                )
                if not exists:
                    logger.info(f"Item '{item_name}' not found; creating new item.")
                    new_id = await create_item(
                        item,
                        user_id=user_id,
                        client_id=client_id,
                        request=request
                    )
                    item["item_id"] = new_id
                    item_id = new_id

                quantity = float(item.get("quantity", 0))
                unit_price = float(item.get("unit_price", 0))
                amount = quantity * unit_price
                total_amount += amount

                # For Xero, build a line item with required fields.
                # Assume a default AccountCode of "200" and set TaxType based on your business rules.
                tax_type = "OUTPUT" if idx == 0 else "NONE"
                line_items.append({
                    "Description": item.get("description", "No description"),
                    "Quantity": quantity,
                    "UnitAmount": unit_price,
                    "AccountCode": item.get("account_code", "700"),
                    "TaxType": tax_type
                })

            doc_number = generate_doc_number("INV")
            # Construct the Xero invoice payload.
            payload = {
                "Type": "ACCREC",
                "Contact": {
                    "ContactID": valid_customer_id
                    # "Name": customer_name
                },
                "Date": format_date(date),
                "DueDate": format_date(date),  # Optionally adjust to a calculated due date.
                "LineItems": line_items,
                # "InvoiceNumber": doc_number,
                "Status": "AUTHORISED",
                "CurrencyCode": "USD"
            }
            logger.info(f"Invoice payload prepared for InvoiceNumber: {doc_number}")
            # print(payload)
            payload = {"Invoices": [payload]}


            logger.info("About to call xero_request")
            response = await xero_request(

                "Invoices",
                user_id=user_id,
                payload=payload,
                method="POST",

            )
            logger.info(f"Response from Xero invoice creation: {response}")

            if isinstance(response, dict) and response.get("Error"):
                error_msg = f"Unexpected response structure: {response}"
                logger.error(error_msg)
                return {"error": error_msg}

            # Store the invoice record in Firestore.
            if user_id:
                invoice_record = {
                    "integration": "Xero",
                    "transactionType": "Invoice",
                    "transactionId": transaction_id,
                    "invoiceNumber": doc_number,
                    "customerId": valid_customer_id,
                    "date": format_date(date),
                    "amount": total_amount,
                    "metadata": {"syncToken": "0"}
                }
                await asyncio.to_thread(
                    store_invoice_record,
                    user_id,
                    record_type,
                    group_id,
                    transaction_id,
                    invoice_record,
                    client_id=client_id
                )

            return response

        except Exception as e:
            logger.error(f"Exception in send_invoice: {str(e)}")
            return {"error": str(e)}
