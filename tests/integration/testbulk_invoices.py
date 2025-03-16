import unittest
from masyg_extractor.firebase.firebase_init import firebase_init

firebase_init()
from unittest.mock import patch, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
import logging

from masyg_extractor.integrations.services.invoice_service import InvoiceService

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")

app = FastAPI()


@app.get("/dummy")
def dummy():
    return {"message": "dummy"}


class TestBulkSendInvoices(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)
        # Patch the session used in quickbooks_client with required dummy values.
        self.session_patch = patch(
            'masyg_extractor.integrations.quickbooks_client.session',
            new={"access_token": "dummy_access_token", "realm_id": "dummy_realm_id"},
            create=True
        )
        self.mock_session = self.session_patch.start()
        from masyg_extractor.integrations.quickbooks_client import logger
        self.logger = logger

    def tearDown(self):
        self.session_patch.stop()

    def test_bulk_send_invoices(self):
        """
        Process multiple invoices in bulk, covering scenarios:
          - Duplicate invoice (error)
          - Missing items (error)
          - New customer creation
          - Existing customer with item creation
          - Multiple invoices for same customer.
        """
        scenarios = [
            {
                "name": "Duplicate Invoice",
                "customer_id": "A1",
                "customer_name": "CustomerA",
                "items": [{"item_name": "ItemA", "quantity": 2, "unit_price": "10.00", "description": "DescA"}],
                "transaction_id": "dup_invoice",
                "user_id": "user1",
                "group_id": "groupX",
                "expected_error": "Invoices(dup_invoice) already recorded in QuickBooks.",
                "invoice_exists": True,
            },
            {
                "name": "No Items Provided",
                "customer_id": "B1",
                "customer_name": "CustomerB",
                "items": [],
                "transaction_id": "empty_invoice",
                "user_id": "user1",
                "group_id": "groupX",
                "expected_error": "Items required for invoice creation.",
                "invoice_exists": False,
            },
            {
                "name": "New Customer Creation",
                "customer_id": None,
                "customer_name": "CustomerC",
                "items": [{"item_name": "ItemC", "quantity": 1, "unit_price": "15.00", "description": "DescC"}],
                "transaction_id": "invoice_new_customer",
                "user_id": "user1",
                "group_id": "groupX",
                "expected_response": {"Invoice": {"Id": "inv_3"}},
                "get_or_create_customer_return": "C_new",
                "invoice_exists": False,
                "check_item_exists": True,
            },
            {
                "name": "Existing Customer with Item Creation",
                "customer_id": "D1",
                "customer_name": "CustomerD",
                "items": [{"item_name": "ItemD", "quantity": 3, "unit_price": "20.00", "description": "DescD"}],
                "transaction_id": "invoice_existing_customer",
                "user_id": "user1",
                "group_id": "groupX",
                "expected_response": {"Invoice": {"Id": "inv_4"}},
                "get_or_create_customer_return": "D1",
                "invoice_exists": False,
                "check_item_exists": False,  # Triggers item creation.
                "create_item_return": "item_D"
            },
            {
                "name": "Multiple Invoices for Same Customer, Invoice 1",
                "customer_id": "E1",
                "customer_name": "CustomerE",
                "items": [{"item_name": "ItemE", "quantity": 1, "unit_price": "30.00", "description": "DescE"}],
                "transaction_id": "invoice_E1_1",
                "user_id": "user1",
                "group_id": "groupX",
                "expected_response": {"Invoice": {"Id": "inv_E"}},
                "get_or_create_customer_return": "E1",
                "invoice_exists": False,
                "check_item_exists": True,
            },
            {
                "name": "Multiple Invoices for Same Customer, Invoice 2",
                "customer_id": "E1",
                "customer_name": "CustomerE",
                "items": [{"item_name": "ItemE", "quantity": 2, "unit_price": "30.00", "description": "DescE"}],
                "transaction_id": "invoice_E1_2",
                "user_id": "user1",
                "group_id": "groupX",
                "expected_response": {"Invoice": {"Id": "inv_E"}},
                "get_or_create_customer_return": "E1",
                "invoice_exists": False,
                "check_item_exists": True,
            },
        ]

        for sc in scenarios:
            with self.subTest(scenario=sc["name"]):
                # Patch the functions in the namespace of invoice_service.
                with patch(
                        'masyg_extractor.integrations.services.invoice_service.invoice_exists_in_firestore') as mock_invoice_exists, \
                        patch(
                            'masyg_extractor.integrations.services.invoice_service.get_or_create_customer') as mock_get_or_create_customer, \
                        patch(
                            'masyg_extractor.integrations.services.invoice_service.check_item_exists') as mock_check_item_exists, \
                        patch('masyg_extractor.integrations.services.invoice_service.create_item') as mock_create_item, \
                        patch(
                            'masyg_extractor.integrations.services.invoice_service.quickbooks_request') as mock_quickbooks_request, \
                        patch(
                            'masyg_extractor.integrations.services.invoice_service.store_invoice_record') as mock_store_invoice_record:

                    # Create a dummy request object.
                    dummy_request = MagicMock()

                    mock_invoice_exists.return_value = sc.get("invoice_exists", False)
                    if "get_or_create_customer_return" in sc:
                        mock_get_or_create_customer.return_value = sc["get_or_create_customer_return"]
                    else:
                        mock_get_or_create_customer.return_value = sc["customer_id"] or "dummy"

                    mock_check_item_exists.return_value = sc.get("check_item_exists", True)
                    if not mock_check_item_exists.return_value and "create_item_return" in sc:
                        mock_create_item.return_value = sc["create_item_return"]
                    else:
                        mock_create_item.return_value = None

                    if "expected_response" in sc:
                        mock_quickbooks_request.return_value = sc["expected_response"]
                    else:
                        mock_quickbooks_request.return_value = {}

                    response = InvoiceService.send_invoice(
                        dummy_request,
                        sc["customer_name"],
                        sc["customer_id"],
                        sc["items"],
                        sc["transaction_id"],
                        sc["group_id"],
                        sc.get("date", "2023-01-01"),  # Provide a default date.
                        sc["user_id"]
                    )
                    self.logger.info(f"Scenario '{sc['name']}': response: {response}")

                    if "expected_error" in sc:
                        self.assertIn("error", response)
                        self.assertEqual(response["error"], sc["expected_error"])
                    elif "expected_response" in sc:
                        self.assertEqual(response, sc["expected_response"])
                        self.assertTrue(mock_store_invoice_record.called)
                    else:
                        self.assertIsInstance(response, dict)


if __name__ == '__main__':
    unittest.main()
