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

    def disconnect(self) -> None:
        email_address = self.email_address()
        self.release_mailbox_owner(email_address)
        self.integration_ref().delete()
