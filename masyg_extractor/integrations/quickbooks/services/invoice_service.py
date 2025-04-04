import asyncio
from typing import List, Dict, Any, Optional
from fastapi import Request
from masyg_extractor.services.my_log import logger, send_log
from masyg_extractor.integrations.quickbooks.quickbooks_client import quickbooks_request
from masyg_extractor.integrations.quickbooks.repository.firestore_repository import (
    store_invoice_record,
    invoice_exists_in_firestore
)
from masyg_extractor.integrations.quickbooks.services.customer_service import get_or_create_customer
from masyg_extractor.integrations.quickbooks.services.item_service import check_item_exists, create_item
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
        Asynchronously creates an invoice in QuickBooks and stores key invoice info in Firestore.
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
                msg = f"{record_type.capitalize()} for ({get_original_filename(transaction_id)}) already recorded in QuickBooks."
                # Log asynchronously without blocking the current execution.
                await send_log(f"❌ {msg}", user_room=client_id)
                await asyncio.sleep(1)
                return dup

            # Offload customer lookup/creation.
            valid_customer_id = await get_or_create_customer(
                request,
                customer_id,
                customer_name,
                user_id,
                client_id=client_id
            )
            # logger.info(f"Using customer ID: {valid_customer_id}")
            # print("0k0k0k0k0k")
            if not items:
                logger.info("No items provided for invoice.")
                return {"error": "Items required for invoice creation."}

            line_items = []
            total_amount = 0.0
            for idx, item in enumerate(items):
                # print("0k0k0k0k0k")
                item_name = item.get("item_name")
                item_id = item.get("item_id")
                exists = await check_item_exists(
                    item_name,
                    item_id,
                    client_id=client_id,
                    request=request
                )
                if not exists:
                    logger.info(f"Item '{item_name}' not found; creating new item.")
                    new_id = await create_item(
                        item,
                        client_id=client_id,
                        request=request
                    )
                    item["item_id"] = new_id
                    item_id = new_id

                quantity = float(item.get("quantity", 0))
                unit_price = float(item.get("unit_price", 0))
                amount = quantity * unit_price
                total_amount += amount
                tax_code = "TAX" if idx == 0 else "NON"
                line_items.append({
                    "DetailType": "SalesItemLineDetail",
                    "Amount": amount,
                    "Description": item.get("description", "No description"),
                    "SalesItemLineDetail": {
                        "ItemRef": {"value": item_id},
                        "Qty": int(quantity),
                        "UnitPrice": unit_price,
                        "TaxCodeRef": {"value": tax_code}
                    }
                })


            doc_number = generate_doc_number("INV")
            payload = {
                "CustomerRef": {"value": valid_customer_id, "name": customer_name},
                "AutoDocNumber": True,
                "EmailStatus": "NotSet",
                "Line": line_items,
                "TotalAmt": total_amount,
                "TxnDate": date,
                "CurrencyRef": {"value": "USD"},
                "PrintStatus": "NeedToPrint",
                "DocNumber": doc_number
            }
            logger.info(f"Invoice payload prepared for doc_number: {doc_number}")

            logger.info("About to call quickbooks_request")
            # Offload the synchronous HTTP request.
            response = await quickbooks_request(
                request,
                "invoice",
                payload=payload,
                method="POST",
                client_id=client_id)
            logger.info(f"response from quickbook request: {response}")

            if isinstance(response, dict) and "fault" in response:
                error_msg = f"Unexpected response structure: {response}"
                logger.error(error_msg)
                return {"error": error_msg}

            if user_id:
                invoice_record = {
                    "integration": "QuickBooks",
                    "transactionType": "Invoice",
                    "transactionId": transaction_id,
                    "docNumber": doc_number,
                    "customerId": valid_customer_id,
                    "date": date,
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
