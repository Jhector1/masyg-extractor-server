import unittest
import time
from unittest.mock import patch, MagicMock
from datetime import datetime
import logging

# Ensure Firebase Admin is initialized so that firestore.client() can work.
import firebase_admin
try:
    firebase_admin.get_app()
except ValueError:
    # Initialize with dummy options for testing.
    firebase_admin.initialize_app(options={'projectId': 'dummy-project'})

# Import firebase_init and InvoiceService after Firebase is initialized.
from masyg_extractor.firebase.firebase_init import firebase_init
from masyg_extractor.integrations.services.invoice_service import InvoiceService

# Optionally call firebase_init() if your module performs additional setup.
firebase_init()

# Configure logging.
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")

class DummyRequest:
    """
    A simple dummy request object to simulate FastAPI's Request with a session attribute.
    """
    def __init__(self):
        self.session = {"access_token": "dummy_access_token", "realm_id": "dummy_realm_id"}

class TestBatchInvoicesEfficiency(unittest.TestCase):
    def setUp(self):
        # Create a dummy request with a session dictionary.
        self.request = DummyRequest()
        self.logger = logging.getLogger("TestBatchInvoicesEfficiency")

    def tearDown(self):
        pass

    @patch('masyg_extractor.integrations.services.invoice_service.invoice_exists_in_firestore')
    @patch('masyg_extractor.integrations.services.invoice_service.quickbooks_request')
    @patch('masyg_extractor.integrations.services.invoice_service.get_or_create_customer')
    @patch('masyg_extractor.integrations.services.invoice_service.check_item_exists')
    @patch('masyg_extractor.integrations.services.invoice_service.create_item')
    @patch('masyg_extractor.integrations.services.invoice_service.store_invoice_record')
    def test_batch_invoices_efficiency(
        self,
        mock_store_record,
        mock_create_item,
        mock_check_item_exists,
        mock_get_or_create_customer,
        mock_quickbooks_request,
        mock_record_exists
    ):
        """
        Test processing a batch of invoices to measure efficiency.
        This test simulates multiple scenarios in bulk with patched dependencies.
        It measures total and average execution time, logs the results, and asserts
        that the batch processing time is below a threshold.
        """
        # Configure mocks:
        mock_record_exists.return_value = False
        valid_customer_id = "123"
        mock_get_or_create_customer.return_value = valid_customer_id
        # Assume that the item exists so no need to create an item.
        mock_check_item_exists.return_value = True
        mock_create_item.return_value = None
        # Simulate a dummy QuickBooks response.
        dummy_response = {"Invoice": {"Id": "inv_dummy"}}
        mock_quickbooks_request.return_value = dummy_response

        num_invoices = 100  # Process 100 invoices in batch.
        start_time = time.perf_counter()

        for i in range(num_invoices):
            transaction_id = f"invoice_{i}"
            response = InvoiceService.send_invoice(
                request=self.request,
                customer_name="Batch Customer",
                customer_id="123",
                items=[{
                    "item_name": "BulkItem",
                    "quantity": 2,
                    "unit_price": 10.00,
                    "description": "Bulk Desc"
                }],
                transaction_id=transaction_id,
                group_id="batchGroup",
                date="2025-02-01",
                user_id="user_batch"
            )
            # Log each invoice result.
            self.logger.info(f"Invoice {transaction_id} response: {response}")
            # Assert that the response is the dummy response.
            self.assertEqual(response, dummy_response)

        end_time = time.perf_counter()
        total_time = end_time - start_time
        avg_time = total_time / num_invoices
        self.logger.info(f"Processed {num_invoices} invoices in {total_time:.4f} seconds (avg {avg_time:.4f} sec/invoice)")

        # Set a threshold (e.g., total processing should be under 1 second).
        self.assertLess(total_time, 1.0, f"Batch processing took too long: {total_time:.4f} seconds")
        # Optionally, assert average time per invoice is below a threshold.
        self.assertLess(avg_time, 0.02, f"Average processing time per invoice is too high: {avg_time:.4f} seconds")

if __name__ == '__main__':
    unittest.main()
