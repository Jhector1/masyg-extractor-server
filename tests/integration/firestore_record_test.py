import asyncio
import logging
from unittest import TestCase
from unittest.mock import patch, MagicMock

# Ensure Firebase Admin is initialized before importing modules that use Firestore.
import firebase_admin
from masyg_extractor.services import global_executor

try:
    firebase_admin.get_app()
except ValueError:
    firebase_admin.initialize_app(options={'projectId': 'dummy-project'})

from masyg_extractor.firebase.firebase_init import firebase_init
firebase_init()

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")

# Set the global executor's MAIN_LOOP.
global_executor.MAIN_LOOP = asyncio.new_event_loop()

class TestFirestoreRecords(TestCase):
    def setUp(self):
        # Patch the MAIN_LOOP in the global executor.
        patcher = patch(
            "masyg_extractor.services.global_executor.MAIN_LOOP",
            new=global_executor.MAIN_LOOP
        )
        self.addCleanup(patcher.stop)
        patcher.start()

        # Import logger from quickbooks_client.
        from masyg_extractor.integrations.quickbooks_client import logger
        self.logger = logger

    # --- Helper methods to set up explicit Firestore chain mocks ---
    def _setup_customer_chain(self, mock_firestore_db, exists_value: bool):
        """
        Sets up the following chain:
            firestore_db.collection("users")
                        .document(user_id)
                        .collection("integrations_legacy")
                        .document("QuickBooks")
                        .collection("customers")
                        .document(customer_id)
        so that calling get().exists returns exists_value.
        """
        users_collection = MagicMock(name="users_collection")
        user_doc = MagicMock(name="user_doc")
        integrations_collection = MagicMock(name="integrations_collection")
        quickbooks_doc = MagicMock(name="quickbooks_doc")
        customers_collection = MagicMock(name="customers_collection")
        customer_doc = MagicMock(name="customer_doc")
        customer_get_result = MagicMock(name="customer_get_result")
        customer_get_result.exists = exists_value
        customer_doc.get.return_value = customer_get_result

        mock_firestore_db.collection.return_value = users_collection
        users_collection.document.return_value = user_doc
        user_doc.collection.return_value = integrations_collection
        integrations_collection.document.return_value = quickbooks_doc
        quickbooks_doc.collection.return_value = customers_collection
        customers_collection.document.return_value = customer_doc

        return customer_doc

    def _setup_invoice_chain(self, mock_firestore_db, exists_value: bool):
        """
        Sets up the following chain:
            firestore_db.collection("users")
                        .document(user_id)
                        .collection("integrations_legacy")
                        .document("QuickBooks")
                        .collection(record_type)
                        .document(group_id)
                        .collection("transactions")
                        .document(transaction_id)
        so that calling get().exists returns exists_value.
        """
        users_collection = MagicMock(name="users_collection")
        user_doc = MagicMock(name="user_doc")
        integrations_collection = MagicMock(name="integrations_collection")
        quickbooks_doc = MagicMock(name="quickbooks_doc")
        record_collection = MagicMock(name="record_collection")
        group_doc = MagicMock(name="group_doc")
        transactions_collection = MagicMock(name="transactions_collection")
        transaction_doc = MagicMock(name="transaction_doc")
        transaction_get_result = MagicMock(name="transaction_get_result")
        transaction_get_result.exists = exists_value
        transaction_doc.get.return_value = transaction_get_result

        mock_firestore_db.collection.return_value = users_collection
        users_collection.document.return_value = user_doc
        user_doc.collection.return_value = integrations_collection
        integrations_collection.document.return_value = quickbooks_doc
        quickbooks_doc.collection.return_value = record_collection
        record_collection.document.return_value = group_doc
        group_doc.collection.return_value = transactions_collection
        transactions_collection.document.return_value = transaction_doc

        return transaction_doc

    # --- Existence Check Tests ---
    @patch("masyg_extractor.integrations.repository.firestore_repository.firestore_db")
    def test_customer_exists_in_firestore(self, mock_firestore_db):
        """Test that Firestore correctly identifies an existing customer."""
        from masyg_extractor.integrations.repository.firestore_repository import customer_exists_in_firestore

        user_id = "user123"
        customer_id = "customer789"

        # Set up chain so that get().exists returns True.
        self._setup_customer_chain(mock_firestore_db, exists_value=True)

        with self.assertLogs(self.logger, level='INFO') as log_capture:
            result = customer_exists_in_firestore(user_id, customer_id)
            self.logger.info(f"Result: {result}")

        self.assertTrue(result)
        self.assertTrue(any("Result:" in record for record in log_capture.output))

    @patch("masyg_extractor.integrations.repository.firestore_repository.firestore_db")
    def test_customer_does_not_exist_in_firestore(self, mock_firestore_db):
        """Test that Firestore correctly identifies a missing customer."""
        from masyg_extractor.integrations.repository.firestore_repository import customer_exists_in_firestore

        user_id = "user123"
        customer_id = "customer789"

        self._setup_customer_chain(mock_firestore_db, exists_value=False)

        with self.assertLogs(self.logger, level='INFO') as log_capture:
            result = customer_exists_in_firestore(user_id, customer_id)
            self.logger.info(f"Result: {result}")

        self.assertFalse(result)
        self.assertTrue(any("Result:" in record for record in log_capture.output))

    @patch("masyg_extractor.integrations.repository.firestore_repository.firestore_db")
    def test_invoice_exists_in_firestore(self, mock_firestore_db):
        """Test that Firestore correctly identifies an existing invoice."""
        from masyg_extractor.integrations.repository.firestore_repository import invoice_exists_in_firestore

        user_id = "user123"
        record_type = "invoices"
        group_id = "groupA"
        transaction_id = "invoice123"

        self._setup_invoice_chain(mock_firestore_db, exists_value=True)

        with self.assertLogs(self.logger, level='INFO') as log_capture:
            result = invoice_exists_in_firestore(user_id, record_type, group_id, transaction_id)
            self.logger.info(f"Result: {result}")

        self.assertTrue(result)
        self.assertTrue(any("Result:" in record for record in log_capture.output))

    @patch("masyg_extractor.integrations.repository.firestore_repository.firestore_db")
    def test_invoice_does_not_exist_in_firestore(self, mock_firestore_db):
        """Test that Firestore correctly identifies a missing invoice."""
        from masyg_extractor.integrations.repository.firestore_repository import invoice_exists_in_firestore

        user_id = "user123"
        record_type = "invoices"
        group_id = "groupA"
        transaction_id = "invoice123"

        self._setup_invoice_chain(mock_firestore_db, exists_value=False)

        with self.assertLogs(self.logger, level='INFO') as log_capture:
            result = invoice_exists_in_firestore(user_id, record_type, group_id, transaction_id)
            self.logger.info(f"Result: {result}")

        self.assertFalse(result)
        self.assertTrue(any("Result:" in record for record in log_capture.output))

    # --- Store Record Tests ---
    @patch("masyg_extractor.integrations.repository.firestore_repository.firestore_db")
    def test_store_customer_record_valid(self, mock_firestore_db):
        """Test that store_customer_record properly stores a customer record in Firestore."""
        from masyg_extractor.integrations.repository.firestore_repository import store_customer_record

        user_id = "user123"
        customer_id = "customer789"
        customer_data = {"DisplayName": "Customer Test"}

        users_collection = MagicMock(name="users_collection")
        user_doc = MagicMock(name="user_doc")
        integrations_collection = MagicMock(name="integrations_collection")
        quickbooks_doc = MagicMock(name="quickbooks_doc")
        customers_collection = MagicMock(name="customers_collection")
        customer_doc = MagicMock(name="customer_doc")

        mock_firestore_db.collection.return_value = users_collection
        users_collection.document.return_value = user_doc
        user_doc.collection.return_value = integrations_collection
        integrations_collection.document.return_value = quickbooks_doc
        quickbooks_doc.collection.return_value = customers_collection
        customers_collection.document.return_value = customer_doc

        with self.assertLogs(self.logger, level='INFO') as log_capture:
            store_customer_record(user_id, customer_id, customer_data, client_id="test_client")
            self.logger.info(f"Result: Stored customer record for {customer_id}")

        customer_doc.set.assert_called_once_with(customer_data)
        self.assertTrue(any("Result:" in record for record in log_capture.output))

    @patch("masyg_extractor.integrations.repository.firestore_repository.firestore_db")
    def test_store_invoice_record_valid(self, mock_firestore_db):
        """Test that store_invoice_record properly stores an invoice record in Firestore."""
        from masyg_extractor.integrations.repository.firestore_repository import store_invoice_record

        user_id = "user123"
        record_type = "invoices"
        group_id = "groupA"
        transaction_id = "invoice123"
        invoice_data = {
            "integrations": "QuickBooks",
            "transactionType": "Invoice",
            "transactionId": transaction_id,
            "docNumber": "INV-123456789",
            "customerId": "customer789",
            "date": "2025-02-01",
            "amount": 100.0,
            "metadata": {"syncToken": "0"}
        }

        users_collection = MagicMock(name="users_collection")
        user_doc = MagicMock(name="user_doc")
        integrations_collection = MagicMock(name="integrations_collection")
        quickbooks_doc = MagicMock(name="quickbooks_doc")
        record_collection = MagicMock(name="record_collection")
        group_doc = MagicMock(name="group_doc")
        transactions_collection = MagicMock(name="transactions_collection")
        transaction_doc = MagicMock(name="transaction_doc")

        mock_firestore_db.collection.return_value = users_collection
        users_collection.document.return_value = user_doc
        user_doc.collection.return_value = integrations_collection
        integrations_collection.document.return_value = quickbooks_doc
        quickbooks_doc.collection.return_value = record_collection
        record_collection.document.return_value = group_doc
        group_doc.collection.return_value = transactions_collection
        transactions_collection.document.return_value = transaction_doc

        with self.assertLogs(self.logger, level='INFO') as log_capture:
            store_invoice_record(user_id, record_type, group_id, transaction_id, invoice_data, client_id="test_client")
            self.logger.info(f"Result: Stored invoice record for {transaction_id}")

        transaction_doc.set.assert_called_once_with(invoice_data)
        self.assertTrue(any("Result:" in record for record in log_capture.output))

    @patch("masyg_extractor.integrations.repository.firestore_repository.firestore_db")
    def test_store_invoice_record_missing_parameters(self, mock_firestore_db):
        """Test that store_invoice_record raises ValueError when required parameters are missing."""
        from masyg_extractor.integrations.repository.firestore_repository import store_invoice_record

        user_id = "user123"
        record_type = ""  # Missing record_type
        group_id = "groupA"
        transaction_id = "invoice123"
        invoice_data = {}

        with self.assertRaises(ValueError):
            store_invoice_record(user_id, record_type, group_id, transaction_id, invoice_data, client_id="test_client")
