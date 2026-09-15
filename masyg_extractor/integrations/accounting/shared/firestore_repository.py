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
        *,
        claim_token: str,
    ) -> bool:
        """
        Atomically reserve the canonical accounting record for one
        unique claim generation.

        ``claim_token`` is generated by the execution owner before the
        claim is attempted. The strict boolean return contract remains
        unchanged.
        """
        token = str(
            claim_token or ""
        ).strip()

        if not token:
            raise ValueError(
                "claim_token is required"
            )

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
            "claimToken": token,
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

    def prepare_provider_dispatch(
        self,
        record_type: str,
        group_id: str,
        transaction_id: str,
        *,
        claim_token: str,
        provider_document_number: str,
    ) -> bool:
        """
        Atomically bind provider correlation to the exact owned claim
        generation before provider-document dispatch may begin.
        """
        token = str(
            claim_token or ""
        ).strip()

        number = str(
            provider_document_number or ""
        ).strip()

        if not token:
            return False

        if not number:
            raise ValueError(
                "provider_document_number is required"
            )

        doc_ref = self._get_transaction_doc_ref(
            record_type,
            group_id,
            transaction_id,
        )

        transaction = self.db.transaction()

        @firestore.transactional
        def _prepare(txn) -> bool:
            snapshot = doc_ref.get(
                transaction=txn
            )

            if not snapshot.exists:
                return False

            current = snapshot.to_dict() or {}

            if current.get("status") != "sending":
                return False

            if (
                str(
                    current.get("claimToken")
                    or ""
                ).strip()
                != token
            ):
                return False

            existing_provider_number = str(
                current.get(
                    "providerDocumentNumber"
                )
                or ""
            ).strip()

            existing_doc_number = str(
                current.get("docNumber")
                or ""
            ).strip()

            for existing in (
                existing_provider_number,
                existing_doc_number,
            ):
                if (
                    existing
                    and existing != number
                ):
                    return False

            updates: Dict[str, Any] = {}

            if existing_doc_number != number:
                updates["docNumber"] = number

            if (
                existing_provider_number
                != number
            ):
                updates[
                    "providerDocumentNumber"
                ] = number

            if not current.get(
                "dispatchPreparedAt"
            ):
                updates[
                    "dispatchPreparedAt"
                ] = _utc_now_iso()

            if updates:
                txn.update(
                    doc_ref,
                    updates,
                )

            return True

        return bool(
            _prepare(transaction)
        )

    def mark_provider_dispatch_started(
        self,
        record_type: str,
        group_id: str,
        transaction_id: str,
        *,
        claim_token: str,
    ) -> bool:
        """
        Atomically mark provider dispatch for the exact owned claim
        generation.
        """
        token = str(
            claim_token or ""
        ).strip()

        if not token:
            return False

        doc_ref = self._get_transaction_doc_ref(
            record_type,
            group_id,
            transaction_id,
        )

        transaction = self.db.transaction()

        @firestore.transactional
        def _mark_started(txn) -> bool:
            snapshot = doc_ref.get(
                transaction=txn
            )

            if not snapshot.exists:
                return False

            current = snapshot.to_dict() or {}

            if current.get("status") != "sending":
                return False

            if (
                str(
                    current.get("claimToken")
                    or ""
                ).strip()
                != token
            ):
                return False

            provider_document_number = str(
                current.get(
                    "providerDocumentNumber"
                )
                or current.get("docNumber")
                or ""
            ).strip()

            if not provider_document_number:
                return False

            if current.get(
                "providerDispatchStartedAt"
            ):
                return True

            if not current.get(
                "dispatchPreparedAt"
            ):
                return False

            txn.update(
                doc_ref,
                {
                    "providerDispatchStartedAt":
                        _utc_now_iso(),
                },
            )

            return True

        return bool(
            _mark_started(transaction)
        )

    def finalize_reconciled_record(
        self,
        record_type: str,
        group_id: str,
        transaction_id: str,
        *,
        claim_token: str,
        provider: str,
        intent: str,
        provider_document_id: str,
        provider_document_number: str,
    ) -> bool:
        """
        Atomically finalize positive reconciliation evidence only when
        the current durable record is the exact claim generation
        observed immediately before the provider lookup.

        This method never creates, releases, retries, or contacts a
        provider.
        """
        token = str(
            claim_token or ""
        ).strip()

        provider_name = str(
            provider or ""
        ).strip().lower()

        accounting_intent = str(
            intent or ""
        ).strip().lower()

        provider_id = str(
            provider_document_id or ""
        ).strip()

        provider_number = str(
            provider_document_number or ""
        ).strip()

        if not token:
            return False

        if not provider_name:
            return False

        if not accounting_intent:
            return False

        if not provider_id:
            return False

        if not provider_number:
            return False

        doc_ref = self._get_transaction_doc_ref(
            record_type,
            group_id,
            transaction_id,
        )

        transaction = self.db.transaction()

        @firestore.transactional
        def _finalize(txn) -> bool:
            snapshot = doc_ref.get(
                transaction=txn
            )

            if not snapshot.exists:
                return False

            current = snapshot.to_dict() or {}

            current_status = str(
                current.get("status") or ""
            ).strip().lower()

            if current_status not in {
                "sending",
                "uncertain",
            }:
                return False

            current_claim_token = str(
                current.get("claimToken")
                or ""
            ).strip()

            if current_claim_token != token:
                return False

            current_provider = str(
                current.get("integration") or ""
            ).strip().lower()

            if current_provider != provider_name:
                return False

            current_action = str(
                current.get("action") or ""
            ).strip().lower()

            if (
                current_action
                and current_action != accounting_intent
            ):
                return False

            current_number = str(
                current.get("providerDocumentNumber")
                or current.get("docNumber")
                or ""
            ).strip()

            if current_number != provider_number:
                return False

            completed_at = _utc_now_iso()

            txn.update(
                doc_ref,
                {
                    "status": "succeeded",
                    "docNumber": provider_number,
                    "providerDocumentId": provider_id,
                    "providerDocumentNumber":
                        provider_number,
                    "claimToken": None,
                    "completedAt": completed_at,
                    "reconciledAt": completed_at,
                    "lastError": None,
                },
            )

            return True

        return bool(
            _finalize(transaction)
        )

    def finalize_record(
        self,
        record_type: str,
        group_id: str,
        transaction_id: str,
        data: Dict[str, Any],
        *,
        claim_token: str,
    ) -> bool:
        """
        Atomically finalize only the exact currently-owned sending
        generation as succeeded.
        """
        token = str(
            claim_token or ""
        ).strip()

        if not token:
            return False

        doc_ref = self._get_transaction_doc_ref(
            record_type,
            group_id,
            transaction_id,
        )

        transaction = self.db.transaction()

        @firestore.transactional
        def _finalize(txn) -> bool:
            snapshot = doc_ref.get(
                transaction=txn
            )

            if not snapshot.exists:
                return False

            current = snapshot.to_dict() or {}

            if current.get("status") != "sending":
                return False

            if (
                str(
                    current.get("claimToken")
                    or ""
                ).strip()
                != token
            ):
                return False

            payload: Dict[str, Any] = {
                **data,
                "status": "succeeded",
                "integration": self.integration,
                "group_id": group_id,
                "transactionId": transaction_id,
                "claimToken": None,
                "completedAt": _utc_now_iso(),
                "lastError": None,
            }

            txn.update(
                doc_ref,
                payload,
            )

            return True

        return bool(
            _finalize(transaction)
        )

    def mark_record_uncertain(
        self,
        record_type: str,
        group_id: str,
        transaction_id: str,
        *,
        claim_token: str,
        error: str,
    ) -> bool:
        """
        Atomically preserve only the exact owned claim generation when
        the provider outcome is ambiguous.
        """
        token = str(
            claim_token or ""
        ).strip()

        if not token:
            return False

        doc_ref = self._get_transaction_doc_ref(
            record_type,
            group_id,
            transaction_id,
        )

        transaction = self.db.transaction()

        @firestore.transactional
        def _mark_uncertain(txn) -> bool:
            snapshot = doc_ref.get(
                transaction=txn
            )

            if not snapshot.exists:
                return False

            current = snapshot.to_dict() or {}

            status = str(
                current.get("status") or ""
            ).strip().lower()

            if status not in {
                "sending",
                "uncertain",
            }:
                return False

            if (
                str(
                    current.get("claimToken")
                    or ""
                ).strip()
                != token
            ):
                return False

            txn.update(
                doc_ref,
                {
                    "status": "uncertain",
                    "lastError": str(
                        error or ""
                    ),
                    "uncertainAt": _utc_now_iso(),
                },
            )

            return True

        return bool(
            _mark_uncertain(transaction)
        )

    def release_record_claim(
        self,
        record_type: str,
        group_id: str,
        transaction_id: str,
        *,
        claim_token: str,
    ) -> bool:
        """
        Atomically release only the exact currently-owned ``sending``
        generation.

        A stale actor cannot delete a newer claim recreated at the same
        deterministic Firestore path.
        """
        token = str(
            claim_token or ""
        ).strip()

        if not token:
            return False

        doc_ref = self._get_transaction_doc_ref(
            record_type,
            group_id,
            transaction_id,
        )

        transaction = self.db.transaction()

        @firestore.transactional
        def _release(txn) -> bool:
            snapshot = doc_ref.get(
                transaction=txn
            )

            if not snapshot.exists:
                return False

            data = snapshot.to_dict() or {}

            if data.get("status") != "sending":
                return False

            if (
                str(
                    data.get("claimToken")
                    or ""
                ).strip()
                != token
            ):
                return False

            txn.delete(
                doc_ref
            )

            return True

        released = bool(
            _release(transaction)
        )

        if released:
            logger.info(
                "Released %s claim for transaction %s "
                "under group %s",
                record_type,
                transaction_id,
                group_id,
            )

        return released

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
