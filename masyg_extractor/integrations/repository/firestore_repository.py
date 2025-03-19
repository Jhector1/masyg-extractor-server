# File: masyg_extractor/integrations/repository/firestore_repository.py
from firebase_admin import firestore
from masyg_extractor.services.my_log import logger, send_log
from typing import Dict, Any
import asyncio
# Initialize the Firestore client once in your app's startup.
firestore_db = firestore.client()

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
    if not user_id or not group_id or not transaction_id or not record_type:
        # asyncio.create_task(
        #     send_log("❌ user_id, record_type, group_id, and transaction_id are required.", user_room=client_id))
        raise ValueError("Missing required Firestore path parameters.")

    doc_ref = (
        firestore_db.collection("users")
        .document(user_id)
        .collection("integrations")
        .document("QuickBooks")
        .collection(record_type)
        .document(group_id)
        .collection("transactions")
        .document(transaction_id)
    )
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
    doc_ref = (
        firestore_db.collection("users")
        .document(user_id)
        .collection("integrations")
        .document("QuickBooks")
        .collection(record_type)
        .document(group_id)
        .collection("transactions")
        .document(transaction_id)
    )
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
        # asyncio.create_task(
        #     send_log("❌ user_id is required to store customer record in Firestore.", user_room=client_id))
        raise ValueError("user_id is required.")

    doc_ref = (
        firestore_db.collection("users")
        .document(user_id)
        .collection("integrations")
        .document("QuickBooks")
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
        .document("QuickBooks")
        .collection("customers")
        .document(customer_id)
    )
    return doc_ref.get().exists
