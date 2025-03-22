import asyncio
from typing import List, Dict, Any, Optional
from fastapi import Request
from masyg_extractor.services.my_log import logger, send_log
from masyg_extractor.integrations.quickbooks_client import quickbooks_request
from masyg_extractor.integrations.repository.firestore_repository import (
    store_bill_record,
    bill_exists_in_firestore
)
from masyg_extractor.integrations.services.vendor_service import get_or_create_vendor
from masyg_extractor.integrations.services.item_service import check_item_exists, create_item
from masyg_extractor.integrations.helper.transaction_helpers import generate_doc_number, check_duplicate_record

class BillService:
    @staticmethod
    def send_bill(
            request: Request,
            vendor_name: str,
            vendor_id: Optional[str],
            items: List[Dict[str, Any]],
            transaction_id: str,
            group_id: str,
            date: str,
            user_id: str,
            record_type: str = "bills",
            client_id: str = ""
    ) -> Dict[str, Any]:
        """
        Creates a bill in QuickBooks and stores key bill info in Firestore.
        """
        try:
            if not group_id or group_id.strip() == "":
                return {"error": "Group ID is required for bill creation."}

            dup = check_duplicate_record(user_id, bill_exists_in_firestore, record_type, group_id, transaction_id, client_id)
            if dup.get("error"):
                return dup

            valid_vendor_id = get_or_create_vendor(request, vendor_id, vendor_name, user_id, client_id=client_id)
            logger.info(f"Using vendor ID: {valid_vendor_id}")

            if not items:
                logger.info("No items provided for bill.")
                return {"error": "Items required for bill creation."}

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
                tax_code = "BILL"  # Tax code for bills.
                line_items.append({
                    "DetailType": "PurchaseLineDetail",
                    "Amount": amount,
                    "Description": item.get("description", "No description"),
                    "PurchaseLineDetail": {
                        "ItemRef": {"value": item_id},
                        "Qty": int(quantity),
                        "UnitPrice": unit_price,
                        "TaxCodeRef": {"value": tax_code}
                    }
                })

            doc_number = generate_doc_number("BILL")
            payload = {
                "VendorRef": {"value": valid_vendor_id, "name": vendor_name},
                "AutoDocNumber": True,
                "Line": line_items,
                "TotalAmt": total_amount,
                "TxnDate": date,
                "CurrencyRef": {"value": "USD"},
                "DocNumber": doc_number
            }
            logger.info(f"Bill payload prepared for doc_number: {doc_number}")

            response = quickbooks_request(request, "bill", payload=payload, method="POST", client_id=client_id)
            if isinstance(response, dict) and "fault" in response:
                error_msg = f"Unexpected response structure: {response}"
                logger.error(error_msg)
                return {"error": error_msg}

            if user_id:
                bill_record = {
                    "integration": "QuickBooks",
                    "transactionType": "Bill",
                    "transactionId": transaction_id,
                    "docNumber": doc_number,
                    "vendorId": valid_vendor_id,
                    "date": date,
                    "amount": total_amount,
                    "metadata": {"syncToken": "0"}
                }
                store_bill_record(user_id, record_type, group_id, transaction_id, bill_record, client_id=client_id)

            return response

        except Exception as e:
            logger.error(f"Exception in send_bill: {str(e)}")
            return {"error": str(e)}
