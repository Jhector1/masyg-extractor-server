import time
import random
import asyncio
from masyg_extractor.services.my_log import send_log

def generate_doc_number(prefix: str) -> str:
    """Generate a unique document number with the given prefix."""
    return f"{prefix}-{int(time.time() * 1000)}-{random.randint(100, 999)}"

def check_duplicate_record(user_id: str, exists_fn, record_type: str, group_id: str, transaction_id: str, client_id: str) -> dict:
    """
    Checks if a transaction record already exists in Firestore.
    Returns an error dict if found.
    """
    if user_id and exists_fn(user_id, record_type, group_id, transaction_id):
        msg = f"{record_type.capitalize()}({transaction_id}) already recorded in QuickBooks."
        # Log asynchronously without blocking the current execution.
        # asyncio.create_task(send_log(f"❌ {msg}", user_room=client_id))
        return {"error": msg}
    return {}
