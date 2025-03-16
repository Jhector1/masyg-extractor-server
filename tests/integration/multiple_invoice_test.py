import unittest
from unittest.mock import patch, MagicMock
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from starlette.requests import Request
import logging
from starlette.middleware.sessions import SessionMiddleware

# Initialize Firebase before importing modules that rely on it.
from masyg_extractor.firebase.firebase_init import firebase_init

firebase_init()

# Now import modules that use Firebase.
from masyg_extractor.integrations.services.invoice_service import InvoiceService
from masyg_extractor.integrations.quickbooks_client import logger

# Set up logging.
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")

# Create a dummy FastAPI app for testing.
app = FastAPI()
# Install SessionMiddleware so that request.session is available.
app.add_middleware(SessionMiddleware, secret_key="test_secret_key")


# Simulated session dependency.
def get_dummy_session():
    return {"access_token": "dummy_access_token", "realm_id": "dummy_realm_id"}


@app.get("/dummy")
def dummy_endpoint(dummy_session: dict = Depends(get_dummy_session)):
    return dummy_session


class TestSendMultipleInvoices(unittest.TestCase):
    def setUp(self):
        # Create a TestClient using our FastAPI app.
        self.client = TestClient(app)
        # Store the dummy session data for use within our tests.
        self.session = get_dummy_session()
        self.logger = logger

    def tearDown(self):
        pass

    @patch('masyg_extractor.integrations.repository.firestore_repository.store_invoice_record')
    @patch('masyg_extractor.integrations.services.customer_service.get_or_create_customer')
    @patch('masyg_extractor.integrations.services.item_service.check_item_exists')
    @patch('masyg_extractor.integrations.services.item_service.create_item')
    @patch('masyg_extractor.integrations.quickbooks_client.quickbooks_request')
    @patch('masyg_extractor.integrations.repository.firestore_repository.invoice_exists_in_firestore')
    def test_send_multiple_invoices(
            self,
            mock_invoice_exists,
            mock_quickbooks_request,
            mock_create_item,
            mock_check_item_exists,
            mock_get_or_create_customer,
            mock_store_invoice_record
    ):
        """
        Test that when QuickBooks returns an unexpected response (authentication failure),
        InvoiceService.send_invoice returns an error response containing expected static substrings,
        and store_invoice_record is NOT called.
        """
        customer_name = "Test Customer"
        customer_id = "123"
        user_id = "user1"
        group_id = "group2"
        items = [{
            "item_name": "Item1",
            "quantity": 2,
            "unit_price": "10.00",
            "description": "Desc"
        }]

        # Configure mocks.
        mock_invoice_exists.return_value = False  # No duplicate invoice.
        valid_customer_id = "123"
        mock_get_or_create_customer.return_value = valid_customer_id
        mock_check_item_exists.return_value = True  # Assume the item already exists.
        mock_create_item.return_value = "item_1"  # Do not trigger item creation.

        # Set up a dummy response simulating an authentication failure.
        dummy_quickbooks_response = {
            "warnings": None,
            "intuitObject": None,
            "fault": {
                "error": [{
                    "message": "message=AuthenticationFailed; errorCode=003200; statusCode=401",
                    "detail": "Malformed bearer token: too short or too long",
                    "code": "3200",
                    "element": None
                }],
                "type": "AUTHENTICATION"
            },
            "report": None,
            "queryResponse": None,
            "batchItemResponse": [],
            "attachableResponse": [],
            "syncErrorResponse": None,
            "requestId": None,
            "time": 1741761950669,  # This value is dynamic.
            "status": None,
            "cdcresponse": []
        }
        mock_quickbooks_request.side_effect = lambda endpoint, payload, method, **kwargs: dummy_quickbooks_response

        # Create a dummy request with session included in the ASGI scope.
        dummy_scope = {
            "type": "http",
            "method": "GET",
            "headers": [],
            "session": get_dummy_session()
        }
        dummy_request = Request(dummy_scope)

        # Process multiple invoices.
        invoice_responses = []
        num_invoices = 5
        for i in range(num_invoices):
            transaction_id = f"invoice_{i}"
            response = InvoiceService.send_invoice(
                request=dummy_request,
                customer_name=customer_name,
                customer_id=customer_id,
                items=items,
                transaction_id=transaction_id,
                group_id=group_id,
                date="2023-01-01",               user_id=user_id,
            )
            invoice_responses.append(response)
            self.logger.info(f"Processed invoice {transaction_id}: {response}")

        # Instead of full string equality, verify that the error response contains expected static parts.
        for response in invoice_responses:
            self.assertIn("error", response)
            self.assertTrue(response["error"].startswith("Unexpected response structure:"))
            self.assertIn("'fault':", response["error"])
            self.assertIn("AuthenticationFailed", response["error"])

        # Assert that store_invoice_record is NOT called in error branch.
        self.assertEqual(mock_store_invoice_record.call_count, 0)


if __name__ == '__main__':
    unittest.main()
