# File: masyg_extractor/integrations/services/invoice_service.py
import time
import random
from typing import List, Dict, Any, Optional
from fastapi import Request

from masyg_extractor.services.my_log import logger, send_log
from masyg_extractor.integrations.quickbooks_client import quickbooks_request
# Note: repository functions are now imported from the repository modules.
from masyg_extractor.integrations.repository.firestore_repository import (
    store_invoice_record,
    invoice_exists_in_firestore
)
import asyncio
from masyg_extractor.integrations.services.customer_service import get_or_create_customer
from masyg_extractor.integrations.services.item_service import check_item_exists, create_item

class InvoiceService:
    @staticmethod
    # File: masyg_extractor/integrations/services/invoice_service.py

    def send_invoice(
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
        Creates an invoice in QuickBooks and stores key invoice info in Firestore.
        """
        try:
            if not group_id or group_id.strip() == "":
                return {"error": "Group ID is required for invoice creation."}

            # Check for duplicate invoice in Firestore.
            if user_id and invoice_exists_in_firestore(user_id, record_type, group_id, transaction_id):
                msg = f"{record_type.capitalize()}({transaction_id}) already recorded in QuickBooks."
                asyncio.create_task(
                    send_log(f"❌ {msg}", user_room=client_id))
                return {"error": msg}

            # Ensure valid customer ID (create or fetch existing).
            valid_customer_id = get_or_create_customer(
                request,
                customer_id,
                customer_name,
                user_id,
                client_id=client_id
            )
            logger.info(f"Using customer ID: {valid_customer_id}")

            if not items:
                logger.info("No items provided for invoice.")
                return {"error": "Items required for invoice creation."}

            # Build line items for QuickBooks.
            line_items = []
            total_amount = 0.0
            for idx, item in enumerate(items):
                item_name = item.get("item_name")
                item_id = item.get("item_id")

                # If the item does not exist, create it.
                if not check_item_exists(item_name, item_id, client_id=client_id, request=request):
                    logger.info(f"Item '{item_name}' not found; creating new item.")
                    new_id = create_item(item, client_id=client_id, request=request)
                    item["item_id"] = new_id
                    item_id = new_id

                quantity = float(item.get("quantity", 0))
                unit_price = float(item.get("unit_price", 0))
                amount = quantity * unit_price
                total_amount += amount

                # Example: apply TAX to the first line item, NON to the rest.
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

            # Generate a unique doc number.
            doc_number = f"INV-{int(time.time() * 1000)}-{random.randint(100, 999)}"
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

            # Send the invoice to QuickBooks.
            response = quickbooks_request(request, "invoice", payload=payload, method="POST", client_id=client_id)

            # Check for an unexpected response (such as an authentication failure).
            if isinstance(response, dict) and "fault" in response:
                error_msg = f"Unexpected response structure: {response}"
                logger.error(error_msg)
                return {"error": error_msg}

            # If successful, store the invoice record in Firestore.
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
                store_invoice_record(user_id, record_type, group_id, transaction_id, invoice_record,
                                     client_id=client_id)

            return response

        except Exception as e:
            logger.error(f"Exception in send_invoice: {str(e)}")
            return {"error": str(e)}
