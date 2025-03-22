
# from masyg_extractor.firebase.firebase_init import firebase_init

# firebase_init()
import pytest

# Import services and helpers from your project.
from masyg_extractor.integrations.services.customer_service import (
    CustomerService, get_or_create_customer
)
from masyg_extractor.integrations.services.vendor_service import (
    VendorService, get_or_create_vendor
)
from masyg_extractor.integrations.services.receipt_service import ReceiptService
from masyg_extractor.integrations.services.bill_service import BillService
from masyg_extractor.integrations.services.invoice_service import InvoiceService
from masyg_extractor.integrations.services.item_service import (
    check_item_exists, create_item, ItemService
)
from masyg_extractor.integrations.repository.firestore_repository import (
    store_customer_record, customer_exists_in_firestore,
    store_vendor_record, vendor_exists_in_firestore,
    store_receipt_record, receipt_exists_in_firestore,
    store_bill_record, bill_exists_in_firestore,
    store_invoice_record, invoice_exists_in_firestore
)
from masyg_extractor.integrations.quickbooks_client import quickbooks_request

# Import the transaction helpers
from masyg_extractor.integrations.helper.transaction_helpers import generate_doc_number, check_duplicate_record

# ----
# Create a dummy Request object for testing.
class DummyRequest:
    def __init__(self, session=None, cookies=None):
        self.session = session or {}
        self.cookies = cookies or {}

# Global dummy Firestore storage for testing.
firestore_storage = {
    "customers": {},
    "vendors": {},
    "receipts": {},
    "bills": {},
    "invoices": {}
}

# ----
# Fake implementations for quickbooks_request.

def fake_quickbooks_request(request, endpoint, payload=None, params=None, method="GET", client_id=""):
    # Simulate responses based on the endpoint.
    if endpoint == "customer":
        if method == "POST":
            # Return a dummy customer creation response.
            return {"Customer": {"Id": "cust_001", "DisplayName": payload.get("DisplayName")}}
    elif endpoint == "vendor":
        if method == "POST":
            return {"Vendor": {"Id": "vend_001", "DisplayName": payload.get("DisplayName")}}
    elif endpoint == "receipt":
        if method == "POST":
            return {"Receipt": {"Id": "rec_001", "DocNumber": payload.get("DocNumber")}}
    elif endpoint == "bill":
        if method == "POST":
            return {"Bill": {"Id": "bill_001", "DocNumber": payload.get("DocNumber")}}
    elif endpoint == "invoice":
        if method == "POST":
            return {"Invoice": {"Id": "inv_001", "DocNumber": payload.get("DocNumber")}}
    elif endpoint == "query":
        query = params.get("query", "")
        # For Customer queries.
        if "FROM Customer" in query:
            if "Test Customer" in query:
                return {"QueryResponse": {"Customer": [{"Id": "cust_001", "DisplayName": "Test Customer"}]}}
            return {"QueryResponse": {}}
        # For Vendor queries.
        if "FROM Vendor" in query:
            if "Test Vendor" in query:
                return {"QueryResponse": {"Vendor": [{"Id": "vend_001", "DisplayName": "Test Vendor"}]}}
            return {"QueryResponse": {}}
        # For Item queries.
        if "FROM Item" in query:
            if "Existing Item" in query:
                return {"QueryResponse": {"Item": [{"Id": "item_001", "Name": "Existing Item"}]}}
            return {"QueryResponse": {}}
        # For Account queries in ItemService.
        if "FROM Account" in query:
            # Return dummy income and expense account IDs.
            return {"QueryResponse": {"Account": [
                {"Id": "acc_income", "AccountType": "Income", "AccountSubType": "ServiceFeeIncome"},
                {"Id": "acc_expense", "AccountType": "Cost of Goods Sold"}
            ]}}
    elif endpoint == "item":
        if method == "POST":
            return {"Item": {"Id": "item_new", "Name": payload.get("Name")}}
    return {}

# ----
# Fake implementations for Firestore repository functions.

def fake_store_customer_record(user_id, customer_id, customer_data, client_id=""):
    firestore_storage["customers"][customer_id] = customer_data

def fake_customer_exists_in_firestore(user_id, customer_id):
    return customer_id in firestore_storage["customers"]

def fake_store_vendor_record(user_id, vendor_id, vendor_data, client_id=""):
    firestore_storage["vendors"][vendor_id] = vendor_data

def fake_vendor_exists_in_firestore(user_id, vendor_id):
    return vendor_id in firestore_storage["vendors"]

def fake_store_receipt_record(user_id, record_type, group_id, transaction_id, data, client_id=""):
    firestore_storage["receipts"][transaction_id] = data

def fake_receipt_exists_in_firestore(user_id, record_type, group_id, transaction_id):
    return transaction_id in firestore_storage["receipts"]

def fake_store_bill_record(user_id, record_type, group_id, transaction_id, data, client_id=""):
    firestore_storage["bills"][transaction_id] = data

def fake_bill_exists_in_firestore(user_id, record_type, group_id, transaction_id):
    return transaction_id in firestore_storage["bills"]

def fake_store_invoice_record(user_id, record_type, group_id, transaction_id, data, client_id=""):
    firestore_storage["invoices"][transaction_id] = data

def fake_invoice_exists_in_firestore(user_id, record_type, group_id, transaction_id):
    return transaction_id in firestore_storage["invoices"]

# ----
# Pytest fixtures to monkeypatch dependencies.

@pytest.fixture(autouse=True)
def patch_quickbooks_request(monkeypatch):
    monkeypatch.setattr("masyg_extractor.integrations.quickbooks_client.quickbooks_request", fake_quickbooks_request)

@pytest.fixture(autouse=True)
def patch_firestore_repository(monkeypatch):
    monkeypatch.setattr("masyg_extractor.integrations.repository.firestore_repository.store_customer_record", fake_store_customer_record)
    monkeypatch.setattr("masyg_extractor.integrations.repository.firestore_repository.customer_exists_in_firestore", fake_customer_exists_in_firestore)
    monkeypatch.setattr("masyg_extractor.integrations.repository.firestore_repository.store_vendor_record", fake_store_vendor_record)
    monkeypatch.setattr("masyg_extractor.integrations.repository.firestore_repository.vendor_exists_in_firestore", fake_vendor_exists_in_firestore)
    monkeypatch.setattr("masyg_extractor.integrations.repository.firestore_repository.store_receipt_record", fake_store_receipt_record)
    monkeypatch.setattr("masyg_extractor.integrations.repository.firestore_repository.receipt_exists_in_firestore", fake_receipt_exists_in_firestore)
    monkeypatch.setattr("masyg_extractor.integrations.repository.firestore_repository.store_bill_record", fake_store_bill_record)
    monkeypatch.setattr("masyg_extractor.integrations.repository.firestore_repository.bill_exists_in_firestore", fake_bill_exists_in_firestore)
    monkeypatch.setattr("masyg_extractor.integrations.repository.firestore_repository.store_invoice_record", fake_store_invoice_record)
    monkeypatch.setattr("masyg_extractor.integrations.repository.firestore_repository.invoice_exists_in_firestore", fake_invoice_exists_in_firestore)

@pytest.fixture
def dummy_request():
    # Create a dummy request with a session and cookies.
    return DummyRequest(session={"user": {"userId": "user_001"}}, cookies={"clientId": "TestClient"})

# ----
# Tests for CustomerService

def test_get_or_create_customer_new(dummy_request):
    # Clear storage
    firestore_storage["customers"].clear()

    # No customer_id provided, so it should create a new customer.
    customer_id = get_or_create_customer(dummy_request, None, "Test Customer", "user_001", client_id="TestClient")
    assert customer_id == "cust_001"
    # Check that the customer is stored in our fake Firestore.
    assert "cust_001" in firestore_storage["customers"]

def test_get_or_create_customer_existing(dummy_request):
    firestore_storage["customers"].clear()
    # Pre-store a customer record.
    fake_store_customer_record("user_001", "cust_001", {"Id": "cust_001", "DisplayName": "Test Customer"})
    # Now get_or_create should return the existing customer.
    customer_id = get_or_create_customer(dummy_request, "cust_001", "Test Customer", "user_001", client_id="TestClient")
    assert customer_id == "cust_001"

# ----
# Tests for VendorService

def test_get_or_create_vendor_new(dummy_request):
    firestore_storage["vendors"].clear()
    vendor_id = get_or_create_vendor(dummy_request, None, "Test Vendor", "user_001", client_id="TestClient")
    assert vendor_id == "vend_001"
    assert "vend_001" in firestore_storage["vendors"]

def test_get_or_create_vendor_existing(dummy_request):
    firestore_storage["vendors"].clear()
    fake_store_vendor_record("user_001", "vend_001", {"Id": "vend_001", "DisplayName": "Test Vendor"})
    vendor_id = get_or_create_vendor(dummy_request, "vend_001", "Test Vendor", "user_001", client_id="TestClient")
    assert vendor_id == "vend_001"

# ----
# Tests for ReceiptService

def test_send_receipt_success(dummy_request):
    # Clear receipts storage.
    firestore_storage["receipts"].clear()
    # Prepare sample receipt parameters.
    response = ReceiptService.send_receipt(
        request=dummy_request,
        customer_name="Test Customer",
        customer_id=None,
        items=[
            {"item_name": "Existing Item", "quantity": 2, "unit_price": 10.0}
        ],
        transaction_id="txn_rec_001",
        group_id="group_001",
        date="2023-01-01",
        user_id="user_001",
        client_id="TestClient"
    )
    # Check that a receipt was created (doc number starts with REC-).
    assert "Receipt" in response
    doc_number = response["Receipt"]["DocNumber"]
    assert doc_number.startswith("REC-")
    # Verify it is stored in Firestore.
    assert "txn_rec_001" in firestore_storage["receipts"]

def test_send_receipt_duplicate(dummy_request):
    # Pre-store a receipt record to simulate duplicate.
    firestore_storage["receipts"]["txn_rec_dup"] = {"dummy": "data"}
    response = ReceiptService.send_receipt(
        request=dummy_request,
        customer_name="Test Customer",
        customer_id="cust_001",
        items=[{"item_name": "Existing Item", "quantity": 1, "unit_price": 5.0}],
        transaction_id="txn_rec_dup",
        group_id="group_001",
        date="2023-01-01",
        user_id="user_001",
        client_id="TestClient"
    )
    assert "error" in response
    assert "already recorded" in response["error"]

# ----
# Tests for BillService

def test_send_bill_success(dummy_request):
    firestore_storage["bills"].clear()
    response = BillService.send_bill(
        request=dummy_request,
        vendor_name="Test Vendor",
        vendor_id=None,
        items=[
            {"item_name": "Existing Item", "quantity": 3, "unit_price": 20.0}
        ],
        transaction_id="txn_bill_001",
        group_id="group_002",
        date="2023-02-01",
        user_id="user_001",
        client_id="TestClient"
    )
    assert "Bill" in response
    doc_number = response["Bill"]["DocNumber"]
    assert doc_number.startswith("BILL-")
    assert "txn_bill_001" in firestore_storage["bills"]

def test_send_bill_duplicate(dummy_request):
    firestore_storage["bills"]["txn_bill_dup"] = {"dummy": "data"}
    response = BillService.send_bill(
        request=dummy_request,
        vendor_name="Test Vendor",
        vendor_id="vend_001",
        items=[{"item_name": "Existing Item", "quantity": 1, "unit_price": 15.0}],
        transaction_id="txn_bill_dup",
        group_id="group_002",
        date="2023-02-01",
        user_id="user_001",
        client_id="TestClient"
    )
    assert "error" in response
    assert "already recorded" in response["error"]

# ----
# Tests for InvoiceService

def test_send_invoice_success(dummy_request):
    firestore_storage["invoices"].clear()
    response = InvoiceService.send_invoice(
        request=dummy_request,
        customer_name="Test Customer",
        customer_id=None,
        items=[
            {"item_name": "Existing Item", "quantity": 4, "unit_price": 30.0}
        ],
        transaction_id="txn_inv_001",
        group_id="group_003",
        date="2023-03-01",
        user_id="user_001",
        client_id="TestClient"
    )
    assert "Invoice" in response
    doc_number = response["Invoice"]["DocNumber"]
    assert doc_number.startswith("INV-")
    assert "txn_inv_001" in firestore_storage["invoices"]

def test_send_invoice_duplicate(dummy_request):
    firestore_storage["invoices"]["txn_inv_dup"] = {"dummy": "data"}
    response = InvoiceService.send_invoice(
        request=dummy_request,
        customer_name="Test Customer",
        customer_id="cust_001",
        items=[{"item_name": "Existing Item", "quantity": 2, "unit_price": 25.0}],
        transaction_id="txn_inv_dup",
        group_id="group_003",
        date="2023-03-01",
        user_id="user_001",
        client_id="TestClient"
    )
    assert "error" in response
    assert "already recorded" in response["error"]

# ----
# Tests for ItemService

def test_check_item_exists_true(dummy_request):
    # For an item with name "Existing Item", fake_quickbooks_request returns a result.
    exists = ItemService.check_item_exists(dummy_request, "Existing Item", None, client_id="TestClient")
    assert exists is True

def test_check_item_exists_false(dummy_request):
    exists = ItemService.check_item_exists(dummy_request, "Nonexistent Item", None, client_id="TestClient")
    assert exists is False

def test_create_item_success(dummy_request):
    # Prepare item data without accounts, so defaults will be fetched.
    item_data = {
        "item_name": "New Service",
        "description": "Test service item"
    }
    item_id = create_item(item_data, client_id="TestClient", request=dummy_request)
    assert item_id == "item_new"

# ----
# Tests for transaction_helpers

def test_generate_doc_number():
    doc = generate_doc_number("TEST")
    assert doc.startswith("TEST-")
    parts = doc.split("-")
    assert len(parts) == 3

def test_check_duplicate_record(monkeypatch):
    # Create a dummy exists function that returns True.
    dummy_exists_fn = lambda user_id, record_type, group_id, txn_id: True
    dup = check_duplicate_record("user_001", dummy_exists_fn, "dummy", "group_001", "txn_dup", "TestClient")
    assert "error" in dup
    assert "already recorded" in dup["error"]

