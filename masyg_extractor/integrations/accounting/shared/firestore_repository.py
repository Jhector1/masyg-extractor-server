from firebase_admin import firestore
from datetime import datetime, timedelta, timezone
from typing import Dict, Any

from google.api_core.exceptions import AlreadyExists

from masyg_extractor.services.my_log import logger

# Keep one shared Firestore client, but do not create it at module-import
# time. Unit tests and CLI tooling must be able to import repository types
# without requiring an initialized Firebase application.
_FIRESTORE_DB = None


def _get_firestore_db():
    global _FIRESTORE_DB

    if _FIRESTORE_DB is None:
        _FIRESTORE_DB = firestore.client()

    return _FIRESTORE_DB


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


class FirestoreRepository:
    """
    A reusable base repository for common Firestore operations.
    """

    def __init__(self, user_id: str, integration: str):

        if not user_id:
            raise ValueError("user_id is required.")
        if not integration:
            raise ValueError("integration name is required.")
        self.user_id = user_id
        self.integration = integration
        self.db = _get_firestore_db()

    def _get_doc_ref(self, *path_segments) -> Any:
        """
        Build a Firestore document reference from a list of path segments.
        For example, _get_doc_ref("users", user_id, "integrations", integration)
        """
        ref = self.db.collection(path_segments[0])
        for segment in path_segments[1:]:
            # Alternate between document and collection calls based on the hierarchy.
            # This simplistic approach assumes an even number of segments implies document references.
            ref = ref.document(segment) if ref._path[-1] != segment else ref.collection(segment)
            # Alternatively, you could build a more robust method if needed.
        return ref

    def store_document(self, doc_ref, data: Dict[str, Any], merge: bool = False) -> None:
        """
        Set data on a document reference.
        """
        doc_ref.set(data, merge=merge)
        logger.info(f"Stored document at path: {doc_ref.path}")

    def document_exists(self, doc_ref) -> bool:
        """
        Check if a document exists.
        """
        exists = doc_ref.get().exists
        logger.info(f"Document exists check at path: {doc_ref.path}: {exists}")
        return exists


class QuickBooksFirestoreService(FirestoreRepository):
    """
    A specialized Firestore repository for QuickBooks integrations.
    """

    def __init__(self, user_id: str, integration):
        # 'quickbooks' is the integration identifier.
        super().__init__(user_id, integration=integration.lower())

    def _get_transaction_doc_ref(self, record_type: str, group_id: str, transaction_id: str):
        """
        Generate a Firestore document reference for a transaction record.
        Path: users/{user_id}/integrations/quickbooks/{record_type}/{group_id}/transactions/{transaction_id}
        """
        if not record_type or not group_id or not transaction_id:
            raise ValueError("Missing required Firestore path parameters.")

        return (
            self.db.collection("users")
            .document(self.user_id)
            .collection("integrations")
            .document(self.integration)
            .collection(record_type)
            .document(group_id)
            .collection("transactions")
            .document(transaction_id)
        )

    def store_record(self, record_type: str, group_id: str, transaction_id: str, data: Dict[str, Any]) -> None:
        """
        General method to store a transaction record (invoice, receipt, bill, etc.) in Firestore.
        """
        doc_ref = self._get_transaction_doc_ref(record_type, group_id, transaction_id)
        self.store_document(doc_ref, data)
        logger.info(f"Stored {record_type} record for transaction {transaction_id} under group {group_id}")

    def record_exists(self, record_type: str, group_id: str, transaction_id: str) -> bool:
        """
        Checks if a transaction record exists in Firestore.
        """
        doc_ref = self._get_transaction_doc_ref(record_type, group_id, transaction_id)
        exists = self.document_exists(doc_ref)
        logger.info(f"{record_type.capitalize()} exists check for transaction {transaction_id} under group {group_id}: {exists}")
        return exists

    def get_record(
        self,
        record_type: str,
        group_id: str,
        transaction_id: str,
    ) -> Dict[str, Any] | None:
        """Return the durable accounting record when it exists."""
        doc_ref = self._get_transaction_doc_ref(
            record_type,
            group_id,
            transaction_id,
        )
        snapshot = doc_ref.get()
        if not snapshot.exists:
            return None

        data = snapshot.to_dict() or {}
        return data if isinstance(data, dict) else None

    def claim_record(
        self,
        record_type: str,
        group_id: str,
        transaction_id: str,
        data: Dict[str, Any] | None = None,
    ) -> bool:
        """
        Atomically reserve the canonical accounting record.

        Firestore DocumentReference.create() succeeds only when the
        document does not already exist. That turns the existing
        durable transaction record into the idempotency owner without
        introducing a second persistence hierarchy.
        """
        doc_ref = self._get_transaction_doc_ref(
            record_type,
            group_id,
            transaction_id,
        )

        payload: Dict[str, Any] = {
            **(data or {}),
            "status": "sending",
            "integration": self.integration,
            "group_id": group_id,
            "transactionId": transaction_id,
            "claimedAt": _utc_now_iso(),
        }

        try:
            doc_ref.create(payload)
            logger.info(
                "Claimed %s record for transaction %s under group %s",
                record_type,
                transaction_id,
                group_id,
            )
            return True
        except AlreadyExists:
            logger.info(
                "%s record is already claimed or completed for "
                "transaction %s under group %s",
                record_type.capitalize(),
                transaction_id,
                group_id,
            )
            return False

    def finalize_record(
        self,
        record_type: str,
        group_id: str,
        transaction_id: str,
        data: Dict[str, Any],
    ) -> None:
        """Finalize an owned claim as a successful provider result."""
        doc_ref = self._get_transaction_doc_ref(
            record_type,
            group_id,
            transaction_id,
        )

        payload: Dict[str, Any] = {
            **data,
            "status": "succeeded",
            "integration": self.integration,
            "group_id": group_id,
            "transactionId": transaction_id,
            "completedAt": _utc_now_iso(),
        }
        self.store_document(doc_ref, payload, merge=True)

    def mark_record_uncertain(
        self,
        record_type: str,
        group_id: str,
        transaction_id: str,
        *,
        error: str,
    ) -> None:
        """
        Preserve the claim when the provider outcome is ambiguous.

        A timeout/network failure can happen after the provider created
        the remote object. Keeping the claim prevents an automatic retry
        from silently creating a duplicate.
        """
        doc_ref = self._get_transaction_doc_ref(
            record_type,
            group_id,
            transaction_id,
        )
        self.store_document(
            doc_ref,
            {
                "status": "uncertain",
                "lastError": str(error or ""),
                "uncertainAt": _utc_now_iso(),
            },
            merge=True,
        )

    def release_record_claim(
        self,
        record_type: str,
        group_id: str,
        transaction_id: str,
    ) -> bool:
        """
        Release only an in-flight claim.

        Successful or uncertain records are intentionally never removed
        by this helper.
        """
        doc_ref = self._get_transaction_doc_ref(
            record_type,
            group_id,
            transaction_id,
        )
        snapshot = doc_ref.get()

        if not snapshot.exists:
            return False

        data = snapshot.to_dict() or {}
        if data.get("status") != "sending":
            return False

        doc_ref.delete()
        logger.info(
            "Released %s claim for transaction %s under group %s",
            record_type,
            transaction_id,
            group_id,
        )
        return True

    # Specific transaction record methods
    def store_invoice(self, group_id: str, transaction_id: str, data: Dict[str, Any]) -> None:
        self.store_record("invoices", group_id, transaction_id, data)

    def invoice_exists(self, group_id: str, transaction_id: str) -> bool:
        return self.record_exists("invoices", group_id, transaction_id)

    def store_receipt(self, group_id: str, transaction_id: str, data: Dict[str, Any]) -> None:
        self.store_record("receipts", group_id, transaction_id, data)

    def receipt_exists(self, group_id: str, transaction_id: str) -> bool:
        return self.record_exists("receipts", group_id, transaction_id)

    def store_bill(self, group_id: str, transaction_id: str, data: Dict[str, Any]) -> None:
        self.store_record("bills", group_id, transaction_id, data)

    def bill_exists(self, group_id: str, transaction_id: str) -> bool:
        return self.record_exists("bills", group_id, transaction_id)

    # Customer operations
    def store_customer(self, customer_id: str, customer_data: Dict[str, Any]) -> None:
        """
        Stores customer data under users/{user_id}/integrations/quickbooks/customers/{customer_id}
        """
        doc_ref = (
            self.db.collection("users")
            .document(self.user_id)
            .collection("integrations")
            .document(self.integration)
            .collection("customers")
            .document(customer_id)
        )
        self.store_document(doc_ref, customer_data)
        logger.info(f"Stored customer record for customerId: {customer_id}")

    def customer_exists(self, customer_id: str) -> bool:
        doc_ref = (
            self.db.collection("users")
            .document(self.user_id)
            .collection("integrations")
            .document(self.integration.lower())
            .collection("customers")
            .document(customer_id)
        )
        return self.document_exists(doc_ref)

    # Vendor operations
    def store_vendor(self, vendor_id: str, vendor_data: Dict[str, Any]) -> None:
        """
        Stores vendor data under users/{user_id}/integrations/quickbooks/vendors/{vendor_id}
        """
        doc_ref = (
            self.db.collection("users")
            .document(self.user_id)
            .collection("integrations")
            .document(self.integration)
            .collection("vendors")
            .document(vendor_id)
        )
        self.store_document(doc_ref, vendor_data)
        logger.info(f"Stored vendor record for vendorId: {vendor_id}")

    def vendor_exists(self, vendor_id: str) -> bool:
        doc_ref = (
            self.db.collection("users")
            .document(self.user_id)
            .collection("integrations")
            .document(self.integration)
            .collection("vendors")
            .document(vendor_id)
        )
        return self.document_exists(doc_ref)

    # Integration token operations
    def store_integration_token(self, access_token: str, refresh_token: str, expires_in: int,  **kwargs) -> None:
        """
        Save QuickBooks tokens and related info under users/{user_id}/integrations/quickbooks.
        """
        token_data = {
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "tokenType": "Bearer",
            "expiresAt": (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat() + "Z",
           **kwargs,
        }
        doc_ref = (
            self.db.collection("users")
            .document(self.user_id)
            .collection("integrations")
            .document(self.integration)
        )
        self.store_document(doc_ref, {"tokenData": token_data}, merge=True)
        logger.info(f"Stored integration token for integration: {self.integration}")
    @staticmethod
    def store_integration_token_statically(user_id: str, access_token: str, refresh_token: str, expires_in: int,
                                integration: str, **kwargs):
        """
        Save QuickBooks tokens and related info in Firestore under the user's integrations.
        """
        token_data = {
            "accessToken": access_token,
            "refreshToken": refresh_token,  # Optionally, encrypt this value
            "tokenType": "Bearer",
            "expiresAt": (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat() + "Z",

            **kwargs,

        }
        doc_ref = _FIRESTORE_DB.collection("users").document(user_id) \
            .collection("integrations").document(integration)
        # Use merge=True to update or create the tokenData field
        doc_ref.set({"tokenData": token_data}, merge=True)

    def get_integration_token(self) -> Dict[str, Any]:
        """
        Retrieves the QuickBooks token data.
        """
        doc_ref = (
            self.db.collection("users")
            .document(self.user_id)
            .collection("integrations")
            .document(self.integration)
        )
        doc = doc_ref.get()
        if doc.exists:
            return doc.to_dict().get("tokenData", {})
        return {}
