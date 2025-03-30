import asyncio
from typing import List, Dict, Any, Optional
from fastapi import Request
from masyg_extractor.services.my_log import logger, send_log
from masyg_extractor.integrations.quickbooks.quickbooks_client import quickbooks_request
from masyg_extractor.integrations.quickbooks.repository.firestore_repository import (
    store_invoice_record,  # Reuse this function for both invoices and receipts
    invoice_exists_in_firestore
)
from masyg_extractor.integrations.quickbooks.services.customer_service import get_or_create_customer
from masyg_extractor.integrations.quickbooks.services.item_service import check_item_exists, create_item
from masyg_extractor.integrations.transaction_helpers import generate_doc_number, check_duplicate_record
from masyg_extractor.utils.tool import get_original_filename


class ReceiptService:
    @staticmethod
    async def send_receipt(
            request: Request,
            customer_name: str,
            customer_id: Optional[str],
            items: List[Dict[str, Any]],
            transaction_id: str,
            group_id: str,
            date: str,
            user_id: str,
            record_type: str = "receipts",  # Use a different record type for receipts
            client_id: str = ""
    ) -> Dict[str, Any]:
        """
        Asynchronously creates a sales receipt in QuickBooks and stores key receipt info in Firestore.
        """
        try:
            if not group_id or group_id.strip() == "":
                return {"error": "Group ID is required for receipt creation."}

            # 1. Check for duplicate record (stop if duplicate exists)
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
                await send_log(f"❌ {msg}", user_room=client_id)
                return dup

            # 2. Lookup or create customer
            valid_customer_id = await get_or_create_customer(
                request,
                customer_id,
                customer_name,
                user_id,
                client_id=client_id
            )

            if not items:
                logger.info("No items provided for receipt.")
                return {"error": "Items required for receipt creation."}

            # 3. Process each item (check existence and create if needed)
            line_items = []
            total_amount = 0.0
            for idx, item in enumerate(items):
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

            # 4. Prepare receipt payload (note differences from invoice)
            doc_number = generate_doc_number("SR")  # Use "SR" prefix for Sales Receipt
            payload = {
                "CustomerRef": {"value": valid_customer_id, "name": customer_name},
                "Line": line_items,
                "TotalAmt": total_amount,
                "TxnDate": date,
                "CurrencyRef": {"value": "USD"},
                "DocNumber": doc_number
                # Optionally, add fields specific to a receipt (e.g., PaymentMethodRef)
            }
            logger.info(f"Receipt payload prepared for doc_number: {doc_number}")

            # 5. Send receipt request to QuickBooks (endpoint type is "salesreceipt")
            logger.info("About to call quickbooks_request for salesreceipt")
            response = await quickbooks_request(
                request,
                "salesreceipt",
                payload=payload,
                method="POST",
                client_id=client_id
            )
            logger.info(f"Response from QuickBooks salesreceipt request: {response}")

            if isinstance(response, dict) and "fault" in response:
                error_msg = f"Unexpected response structure: {response}"
                logger.error(error_msg)
                return {"error": error_msg}

            # 6. Store receipt record in Firestore asynchronously
            if user_id:
                receipt_record = {
                    "integration": "QuickBooks",
                    "transactionType": "SalesReceipt",
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
                    receipt_record,
                    client_id=client_id
                )

            return response

        except Exception as e:
            logger.error(f"Exception in send_receipt: {str(e)}")
            return {"error": str(e)}
