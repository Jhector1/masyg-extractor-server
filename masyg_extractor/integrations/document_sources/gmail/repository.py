from __future__ import annotations

import hashlib

from firebase_admin import firestore

from masyg_extractor.integrations.accounting.shared.token_repository import (
    IntegrationTokenRepository,
)


GMAIL_INTEGRATION_ID = "gmail"
GMAIL_MAILBOX_OWNERS_COLLECTION = "gmail_mailbox_owners"


class GmailMailboxOwnershipError(RuntimeError):
    pass


def _normalized_email(value: str) -> str:
    return str(value or "").strip().lower()


def _mailbox_owner_key(email_address: str) -> str:
    normalized = _normalized_email(email_address)
    if not normalized:
        raise ValueError("Gmail email address is required")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class GmailCredentialRepository:
    """
    Gmail owner over MASYG's canonical encrypted integration-token repository.

    Provider tokens remain owned by IntegrationTokenRepository. Watch/runtime
    metadata is stored beside the encrypted credential payload so token refreshes
    cannot erase it.
    """

    def __init__(self, user_id: str):
        normalized = str(user_id or "").strip()
        if not normalized:
            raise ValueError("user_id is required")

        self.user_id = normalized
        self.tokens = IntegrationTokenRepository(
            self.user_id,
            GMAIL_INTEGRATION_ID,
        )
        self.db = self.tokens.db

    def integration_ref(self):
        return (
            self.db.collection("users")
            .document(self.user_id)
            .collection("integrations")
            .document(GMAIL_INTEGRATION_ID)
        )

    def get_token(self) -> dict:
        return self.tokens.get_integration_token() or {}

    def access_token(self) -> str:
        token = self.get_token()
        return str(
            token.get("accessToken")
            or token.get("access_token")
            or ""
        ).strip()

    def refresh_token(self) -> str:
        token = self.get_token()
        return str(
            token.get("refreshToken")
            or token.get("refresh_token")
            or ""
        ).strip()

    def email_address(self) -> str:
        return _normalized_email(
            self.get_token().get("gmailEmail") or ""
        )

    def history_id(self) -> str:
        return str(
            self.get_token().get("gmailHistoryId") or ""
        ).strip()

    def scope(self) -> str:
        return str(
            self.get_token().get("scope") or ""
        ).strip()

    def is_connected(self) -> bool:
        return bool(
            self.refresh_token()
            and self.email_address()
        )

    def store_token(
        self,
        *,
        access_token: str,
        refresh_token: str,
        expires_in: int,
        scope: str,
        email_address: str,
        history_id: str = "",
    ) -> None:
        self.tokens.store_integration_token(
            access_token,
            refresh_token,
            expires_in,
            scope=str(scope or "").strip(),
            gmailEmail=_normalized_email(email_address),
            gmailHistoryId=str(history_id or "").strip(),
        )

    def store_refreshed_access_token(
        self,
        *,
        access_token: str,
        expires_in: int,
    ) -> None:
        token = self.get_token()
        refresh_token = str(
            token.get("refreshToken")
            or token.get("refresh_token")
            or ""
        ).strip()
        if not refresh_token:
            raise ValueError("Gmail refresh token is missing")

        self.store_token(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
            scope=str(token.get("scope") or "").strip(),
            email_address=str(token.get("gmailEmail") or "").strip(),
            history_id=str(token.get("gmailHistoryId") or "").strip(),
        )

    @classmethod
    def owner_user_id_for_email(
        cls,
        email_address: str,
    ) -> str:
        key = _mailbox_owner_key(email_address)
        snapshot = (
            firestore.client()
            .collection(GMAIL_MAILBOX_OWNERS_COLLECTION)
            .document(key)
            .get()
        )
        if not snapshot.exists:
            return ""

        return str(
            (snapshot.to_dict() or {}).get("userId")
            or ""
        ).strip()

    @classmethod
    def assert_mailbox_available(
        cls,
        *,
        email_address: str,
        user_id: str,
    ) -> None:
        owner = cls.owner_user_id_for_email(email_address)
        normalized_user = str(user_id or "").strip()
        if owner and owner != normalized_user:
            raise GmailMailboxOwnershipError(
                "This Gmail mailbox is already connected "
                "to another MASYG account"
            )

    @classmethod
    def registered_user_ids(cls) -> list[str]:
        user_ids: set[str] = set()
        for snapshot in (
            firestore.client()
            .collection(GMAIL_MAILBOX_OWNERS_COLLECTION)
            .stream()
        ):
            user_id = str(
                (snapshot.to_dict() or {}).get("userId")
                or ""
            ).strip()
            if user_id:
                user_ids.add(user_id)
        return sorted(user_ids)

    def claim_mailbox_owner(
        self,
        email_address: str,
    ) -> bool:
        # Atomically reserve a Gmail mailbox for this MASYG user.
        normalized = _normalized_email(email_address)
        if not normalized:
            raise ValueError("Gmail email address is required")

        ref = (
            self.db.collection(GMAIL_MAILBOX_OWNERS_COLLECTION)
            .document(_mailbox_owner_key(normalized))
        )
        transaction = self.db.transaction()

        @firestore.transactional
        def _claim(txn):
            snapshot = ref.get(transaction=txn)
            owner = ""
            if snapshot.exists:
                owner = str(
                    (snapshot.to_dict() or {}).get("userId")
                    or ""
                ).strip()

            if owner and owner != self.user_id:
                raise GmailMailboxOwnershipError(
                    "This Gmail mailbox is already connected "
                    "to another MASYG account"
                )

            if owner == self.user_id:
                return False

            txn.set(
                ref,
                {
                    "userId": self.user_id,
                    "updatedAt": firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )
            return True

        return bool(_claim(transaction))

    def ensure_mailbox_owner(self) -> None:
        email_address = self.email_address()
        if not email_address:
            raise ValueError(
                "Connected Gmail email address is missing"
            )
        self.claim_mailbox_owner(email_address)

    def release_mailbox_owner(
        self,
        email_address: str,
    ) -> None:
        normalized = _normalized_email(email_address)
        if not normalized:
            return

        ref = (
            self.db.collection(GMAIL_MAILBOX_OWNERS_COLLECTION)
            .document(_mailbox_owner_key(normalized))
        )
        transaction = self.db.transaction()

        @firestore.transactional
        def _release(txn):
            snapshot = ref.get(transaction=txn)
            if not snapshot.exists:
                return

            owner = str(
                (snapshot.to_dict() or {}).get("userId")
                or ""
            ).strip()
            if owner == self.user_id:
                txn.delete(ref)

        _release(transaction)

    def watch_state(self) -> dict:
        snapshot = self.integration_ref().get()
        if not snapshot.exists:
            return {}
        return dict(
            (snapshot.to_dict() or {}).get("gmailWatch")
            or {}
        )

    def store_watch_state(
        self,
        *,
        history_id: str,
        expiration_ms: int,
        topic: str,
    ) -> None:
        self.integration_ref().set(
            {
                "gmailWatch": {
                    "status": "active",
                    "historyId": str(history_id or "").strip(),
                    "expirationMs": int(expiration_ms),
                    "topic": str(topic or "").strip(),
                    "renewedAt": firestore.SERVER_TIMESTAMP,
                }
            },
            merge=True,
        )

    def mark_watch_stopped(self) -> None:
        self.integration_ref().set(
            {
                "gmailWatch": {
                    "status": "stopped",
                    "stoppedAt": firestore.SERVER_TIMESTAMP,
                }
            },
            merge=True,
        )

    def record_notification(
        self,
        *,
        history_id: str,
        message_id: str,
        publish_time: str | None,
    ) -> None:
        normalized_history = str(history_id or "").strip()
        if (
            not normalized_history
            or not normalized_history.isdigit()
        ):
            raise ValueError(
                "Gmail notification historyId is invalid"
            )

        ref = self.integration_ref()
        transaction = self.db.transaction()

        @firestore.transactional
        def _record(txn):
            snapshot = ref.get(transaction=txn)
            if not snapshot.exists:
                raise ValueError(
                    "Gmail integration no longer exists"
                )

            current = dict(
                (snapshot.to_dict() or {}).get(
                    "gmailNotification"
                )
                or {}
            )
            current_history = str(
                current.get("latestHistoryId")
                or ""
            ).strip()

            if (
                current_history.isdigit()
                and int(current_history)
                >= int(normalized_history)
            ):
                return

            txn.set(
                ref,
                {
                    "gmailNotification": {
                        "latestHistoryId": normalized_history,
                        "messageId": str(message_id or "").strip(),
                        "publishTime": publish_time,
                        "receivedAt": firestore.SERVER_TIMESTAMP,
                    }
                },
                merge=True,
            )

        _record(transaction)


    def notification_state(self) -> dict:
        snapshot = self.integration_ref().get()
        if not snapshot.exists:
            return {}
        return dict(
            (snapshot.to_dict() or {}).get("gmailNotification") or {}
        )

    def latest_notification_history_id(self) -> str:
        return str(
            self.notification_state().get("latestHistoryId") or ""
        ).strip()

    def advance_history_id(self, history_id: str) -> None:
        normalized = str(history_id or "").strip()
        if not normalized or not normalized.isdigit():
            raise ValueError("Gmail processed historyId is invalid")

        token = self.get_token()
        access_token = str(
            token.get("accessToken") or token.get("access_token") or ""
        ).strip()
        refresh_token = str(
            token.get("refreshToken") or token.get("refresh_token") or ""
        ).strip()
        email_address = str(token.get("gmailEmail") or "").strip()
        if not access_token or not refresh_token or not email_address:
            raise ValueError("Gmail credential is incomplete")

        token_kwargs = {
            "scope": str(token.get("scope") or "").strip(),
            "gmailEmail": _normalized_email(email_address),
            "gmailHistoryId": normalized,
        }
        expires_at = str(token.get("expiresAt") or "").strip()
        if expires_at:
            token_kwargs["expiresAt"] = expires_at

        self.tokens.store_integration_token(
            access_token,
            refresh_token,
            3600,
            **token_kwargs,
        )

    def claim_processing_lease(self, lease_seconds: int = 1800) -> str:
        from datetime import datetime, timedelta, timezone
        import uuid

        ref = self.integration_ref()
        transaction = self.db.transaction()
        now = datetime.now(timezone.utc)
        lease_token = uuid.uuid4().hex

        @firestore.transactional
        def _claim(txn):
            snapshot = ref.get(transaction=txn)
            if not snapshot.exists:
                return ""

            current = dict(
                (snapshot.to_dict() or {}).get("gmailProcessing") or {}
            )
            lease_until = current.get("leaseUntil")
            if isinstance(lease_until, datetime):
                if lease_until.tzinfo is None:
                    lease_until = lease_until.replace(tzinfo=timezone.utc)
                if (
                    current.get("status") == "running"
                    and lease_until > now
                ):
                    return ""

            txn.set(
                ref,
                {
                    "gmailProcessing": {
                        "status": "running",
                        "token": lease_token,
                        "startedAt": firestore.SERVER_TIMESTAMP,
                        "leaseUntil": now + timedelta(
                            seconds=max(60, int(lease_seconds))
                        ),
                    }
                },
                merge=True,
            )
            return lease_token

        return str(_claim(transaction) or "")

    def release_processing_lease(self, lease_token: str) -> None:
        token = str(lease_token or "").strip()
        if not token:
            return

        ref = self.integration_ref()
        transaction = self.db.transaction()

        @firestore.transactional
        def _release(txn):
            snapshot = ref.get(transaction=txn)
            if not snapshot.exists:
                return

            current = dict(
                (snapshot.to_dict() or {}).get("gmailProcessing") or {}
            )
            if str(current.get("token") or "") != token:
                return

            txn.set(
                ref,
                {
                    "gmailProcessing": {
                        "status": "idle",
                        "token": "",
                        "leaseUntil": None,
                        "finishedAt": firestore.SERVER_TIMESTAMP,
                    }
                },
                merge=True,
            )

        _release(transaction)

    def _import_claim_ref(self, *, message_id: str, part_key: str):
        raw = (
            str(message_id or "").strip()
            + "\0"
            + str(part_key or "").strip()
        )
        if raw == "\0":
            raise ValueError("Gmail import claim identity is required")
        claim_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return (
            self.integration_ref()
            .collection("gmailImports")
            .document(claim_id)
        )

    def claim_attachment(
        self,
        *,
        message_id: str,
        part_key: str,
        filename: str,
        lease_seconds: int = 1800,
    ) -> str:
        from datetime import datetime, timedelta, timezone

        ref = self._import_claim_ref(
            message_id=message_id,
            part_key=part_key,
        )
        transaction = self.db.transaction()
        now = datetime.now(timezone.utc)

        @firestore.transactional
        def _claim(txn):
            snapshot = ref.get(transaction=txn)
            current = (
                snapshot.to_dict() or {}
                if snapshot.exists
                else {}
            )

            if current.get("status") == "processed":
                return "processed"

            lease_until = current.get("leaseUntil")
            if isinstance(lease_until, datetime):
                if lease_until.tzinfo is None:
                    lease_until = lease_until.replace(tzinfo=timezone.utc)
                if (
                    current.get("status") == "processing"
                    and lease_until > now
                ):
                    return "busy"

            txn.set(
                ref,
                {
                    "status": "processing",
                    "messageId": str(message_id or "").strip(),
                    "partKey": str(part_key or "").strip(),
                    "filename": str(filename or "").strip(),
                    "claimedAt": firestore.SERVER_TIMESTAMP,
                    "leaseUntil": now + timedelta(
                        seconds=max(60, int(lease_seconds))
                    ),
                    "attemptCount": int(
                        current.get("attemptCount") or 0
                    ) + 1,
                },
                merge=True,
            )
            return "claimed"

        return str(_claim(transaction) or "busy")

    def group_ingestion_succeeded(
        self,
        group_id: str,
    ) -> bool:
        normalized = str(group_id or "").strip()
        if not normalized:
            return False

        snapshot = (
            self.db.collection("users")
            .document(self.user_id)
            .collection("groups")
            .document(normalized)
            .get()
        )
        if not snapshot.exists:
            return False

        metadata = dict(
            (snapshot.to_dict() or {}).get("metadata")
            or {}
        )
        if metadata.get("status") == "failed":
            return False

        try:
            file_count = int(metadata.get("file_count") or 0)
        except (TypeError, ValueError):
            return False
        return file_count > 0

    def mark_attachment_processed(
        self,
        *,
        message_id: str,
        part_key: str,
        group_id: str,
    ) -> None:
        ref = self._import_claim_ref(
            message_id=message_id,
            part_key=part_key,
        )
        ref.set(
            {
                "status": "processed",
                "groupId": str(group_id or "").strip(),
                "leaseUntil": None,
                "processedAt": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )

    def mark_attachment_failed(
        self,
        *,
        message_id: str,
        part_key: str,
        error_type: str,
    ) -> None:
        ref = self._import_claim_ref(
            message_id=message_id,
            part_key=part_key,
        )
        ref.set(
            {
                "status": "failed",
                "errorType": str(error_type or "").strip(),
                "leaseUntil": None,
                "failedAt": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )

    def mark_sync_recovery_required(
        self,
        *,
        processed_history_id: str,
        latest_history_id: str,
    ) -> None:
        self.integration_ref().set(
            {
                "gmailSync": {
                    "status": "recovery_required",
                    "processedHistoryId": str(
                        processed_history_id or ""
                    ).strip(),
                    "latestHistoryId": str(
                        latest_history_id or ""
                    ).strip(),
                    "updatedAt": firestore.SERVER_TIMESTAMP,
                }
            },
            merge=True,
        )

    def mark_sync_healthy(
        self,
        *,
        processed_history_id: str,
    ) -> None:
        self.integration_ref().set(
            {
                "gmailSync": {
                    "status": "healthy",
                    "processedHistoryId": str(
                        processed_history_id or ""
                    ).strip(),
                    "updatedAt": firestore.SERVER_TIMESTAMP,
                }
            },
            merge=True,
        )

    def _delete_import_claims(self) -> None:
        claims = self.integration_ref().collection("gmailImports")
        for snapshot in claims.stream():
            snapshot.reference.delete()

    def disconnect(self) -> None:
        email_address = self.email_address()
        self.release_mailbox_owner(email_address)
        self._delete_import_claims()
        self.integration_ref().delete()
