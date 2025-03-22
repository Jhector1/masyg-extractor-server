import asyncio
from typing import List, Dict, Any, Optional
from fastapi import Request
from masyg_extractor.services.my_log import logger, send_log
from masyg_extractor.integrations.quickbooks_client import quickbooks_request
from masyg_extractor.integrations.repository.firestore_repository import (
    store_receipt_record,
    receipt_exists_in_firestore
)
from masyg_extractor.integrations.services.customer_service import get_or_create_customer
from masyg_extractor.integrations.services.item_service import check_item_exists, create_item
from masyg_extractor.integrations.helper.transaction_helpers import generate_doc_number, check_duplicate_record

class ReceiptService:
    @staticmethod
    def send_receipt(
            request: Request,
            customer_name: str,
            customer_id: Optional[str],
            items: List[Dict[str, Any]],
            transaction_id: str,
            group_id: str,
            date: str,
            user_id: str,
            record_type: str = "receipts",
            client_id: str = ""
    ) -> Dict[str, Any]:
        """
        Creates a receipt in QuickBooks and stores key receipt info in Firestore.
        """
        try:
            if not group_id or group_id.strip() == "":
                return {"error": "Group ID is required for receipt creation."}

            dup = check_duplicate_record(user_id, receipt_exists_in_firestore, record_type, group_id, transaction_id, client_id)
            if dup.get("error"):
                return dup

            valid_customer_id = get_or_create_customer(request, customer_id, customer_name, user_id, client_id=client_id)
            logger.info(f"Using customer ID: {valid_customer_id}")

            if not items:
                logger.info("No items provided for receipt.")
                return {"error": "Items required for receipt creation."}

            line_items = []
            total_amount = 0.0
            for idx, item in enumerate(items):
                item_name = item.get("item_name")
                item_id = item.get("item_id")
                if not check_item_exists(item_name, item_id, client_id=client_id, request=request):
                    logger.info(f"Item '{item_name}' not found; creating new item.")
                    new_id = create_item(item, client_id=client_id, request=request)
                    item["item_id"] = new_id
                    item_id = new_id

                quantity = float(item.get("quantity", 0))
                unit_price = float(item.get("unit_price", 0))
                amount = quantity * unit_price
                total_amount += amount
                tax_code = "RECEIPT"  # Uniform tax code for receipts.
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

            doc_number = generate_doc_number("REC")
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
            logger.info(f"Receipt payload prepared for doc_number: {doc_number}")

            response = quickbooks_request(request, "receipt", payload=payload, method="POST", client_id=client_id)
            if isinstance(response, dict) and "fault" in response:
                error_msg = f"Unexpected response structure: {response}"
                logger.error(error_msg)
                return {"error": error_msg}

            if user_id:
                receipt_record = {
                    "integration": "QuickBooks",
                    "transactionType": "Receipt",
                    "transactionId": transaction_id,
                    "docNumber": doc_number,
                    "customerId": valid_customer_id,
                    "date": date,
                    "amount": total_amount,
                    "metadata": {"syncToken": "0"}
                }
                store_receipt_record(user_id, record_type, group_id, transaction_id, receipt_record, client_id=client_id)

            return response

        except Exception as e:
            logger.error(f"Exception in send_receipt: {str(e)}")
            return {"error": str(e)}
