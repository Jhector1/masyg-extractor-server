from firebase_admin import firestore
from masyg_extractor.services.my_log import logger, send_log
from typing import Dict, Any
import asyncio

# Initialize the Firestore client once in your app's startup.
firestore_db = firestore.client()


def _get_transaction_doc_ref(user_id: str, record_type: str, group_id: str, transaction_id: str):
    """
    Helper method to generate a Firestore document reference for a transaction record.
    """
    if not user_id or not group_id or not transaction_id or not record_type:
        raise ValueError("Missing required Firestore path parameters.")

    return (
        firestore_db.collection("users")
        .document(user_id)
        .collection("integrations")
        .document("quickbooks")
        .collection(record_type)
        .document(group_id)
        .collection("transactions")
        .document(transaction_id)
    )


def store_invoice_record(
        user_id: str,
        record_type: str,
        group_id: str,
        transaction_id: str,
        data: Dict[str, Any],
        client_id: str = ""
):
    """
    General Firestore storage method for 'invoices', 'receipts', etc.
    """
    doc_ref = _get_transaction_doc_ref(user_id, record_type, group_id, transaction_id)
    doc_ref.set(data)
    logger.info(f"Stored {record_type} record for {transaction_id} under group {group_id}")


def invoice_exists_in_firestore(
        user_id: str,
        record_type: str,
        group_id: str,
        transaction_id: str
) -> bool:
    """
    Checks if a record with the given transaction_id exists in Firestore.
    """
    doc_ref = _get_transaction_doc_ref(user_id, record_type, group_id, transaction_id)
    exists = doc_ref.get().exists
    logger.info(f"{record_type.capitalize()} exists check for {transaction_id} under group {group_id}: {exists}")
    return exists


def store_customer_record(
        user_id: str,
        customer_id: str,
        customer_data: Dict[str, Any],
        client_id: str = ""
):
    """
    Stores customer data in Firestore.
    """
    if not user_id:
        raise ValueError("user_id is required.")

    doc_ref = (
        firestore_db.collection("users")
        .document(user_id)
        .collection("integrations")
        .document("quickbooks")
        .collection("customers")
        .document(customer_id)
    )
    doc_ref.set(customer_data)
    logger.info(f"Stored customer record for customerId: {customer_id}")


def customer_exists_in_firestore(user_id: str, customer_id: str) -> bool:
    """
    Checks if a customer record exists in Firestore.
    """
    doc_ref = (
        firestore_db.collection("users")
        .document(user_id)
        .collection("integrations")
        .document("quickbooks")
        .collection("customers")
        .document(customer_id)
    )
    return doc_ref.get().exists


def store_receipt_record(
        user_id: str,
        record_type: str,
        group_id: str,
        transaction_id: str,
        data: Dict[str, Any],
        client_id: str = ""
):
    """
    General Firestore storage method for 'receipts' (or similar record types).
    """
    doc_ref = _get_transaction_doc_ref(user_id, record_type, group_id, transaction_id)
    doc_ref.set(data)
    logger.info(f"Stored {record_type} record for {transaction_id} under group {group_id}")


def receipt_exists_in_firestore(
        user_id: str,
        record_type: str,
        group_id: str,
        transaction_id: str
) -> bool:
    """
    Checks if a receipt record with the given transaction_id exists in Firestore.
    """
    doc_ref = _get_transaction_doc_ref(user_id, record_type, group_id, transaction_id)
    exists = doc_ref.get().exists
    logger.info(f"{record_type.capitalize()} exists check for {transaction_id} under group {group_id}: {exists}")
    return exists
def store_bill_record(
    user_id: str,
    record_type: str,
    group_id: str,
    transaction_id: str,
    data: Dict[str, Any],
    client_id: str = ""
):
    """
    General Firestore storage method for 'bills' (or similar record types).
    """
    doc_ref = _get_transaction_doc_ref(user_id, record_type, group_id, transaction_id)
    doc_ref.set(data)
    logger.info(f"Stored {record_type} record for {transaction_id} under group {group_id}")


def bill_exists_in_firestore(
    user_id: str,
    record_type: str,
    group_id: str,
    transaction_id: str
) -> bool:
    """
    Checks if a bill record with the given transaction_id exists in Firestore.
    """
    doc_ref = _get_transaction_doc_ref(user_id, record_type, group_id, transaction_id)
    exists = doc_ref.get().exists
    logger.info(f"{record_type.capitalize()} exists check for {transaction_id} under group {group_id}: {exists}")
    return exists
def store_vendor_record(
    user_id: str,
    vendor_id: str,
    vendor_data: Dict[str, Any],
    client_id: str = ""
):
    """
    Stores vendor data in Firestore.
    """
    if not user_id:
        raise ValueError("user_id is required.")

    doc_ref = (
        firestore_db.collection("users")
        .document(user_id)
        .collection("integrations")
        .document("quickbooks")
        .collection("vendors")
        .document(vendor_id)
    )
    doc_ref.set(vendor_data)
    logger.info(f"Stored vendor record for vendorId: {vendor_id}")


def vendor_exists_in_firestore(user_id: str, vendor_id: str) -> bool:
    """
    Checks if a vendor record exists in Firestore.
    """
    doc_ref = (
        firestore_db.collection("users")
        .document(user_id)
        .collection("integrations")
        .document("quickbooks")
        .collection("vendors")
        .document(vendor_id)
    )
    exists = doc_ref.get().exists
    logger.info(f"Vendor exists check for vendorId: {vendor_id} under user {user_id}: {exists}")
    return exists
from firebase_admin import firestore
from datetime import datetime, timedelta

# Initialize Firestore client once


def store_integration_token(user_id: str, access_token: str, refresh_token: str, expires_in: int, realm_id: str, integration: str, **kwargs):
    """
    Save QuickBooks tokens and related info in Firestore under the user's integrations.
    """
    token_data = {
        "accessToken": access_token,
        "refreshToken": refresh_token,  # Optionally, encrypt this value
        "tokenType": "Bearer",
        "expiresAt": (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat() + "Z",
        "realmId": realm_id
    }
    doc_ref = firestore_db.collection("users").document(user_id) \
        .collection("integrations").document(integration)
    # Use merge=True to update or create the tokenData field
    doc_ref.set({"tokenData": token_data}, merge=True)




def get_quickbooks_token(user_id: str, integration: str) -> dict:
    """
    Retrieves QuickBooks token data from Firestore for the given user.

    Returns:
        A dictionary containing token data if it exists, otherwise an empty dict.
    """
    doc_ref = firestore_db.collection("users").document(user_id) \
        .collection("integrations").document(integration)
    doc = doc_ref.get()
    if doc.exists:
        return doc.to_dict().get("tokenData", {})
    return {}
