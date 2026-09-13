from __future__ import annotations

from typing import Any

from firebase_admin import firestore
from datetime import datetime, timedelta
from masyg_extractor.services.my_log import logger


_FIRESTORE_DB = firestore.client()


def get_integration_token(
    user_id: str,
    integration: str,
    *,
    db: Any = None,
) -> dict:
    # Return tokenData for a user's named accounting integration.
    firestore_db = db if db is not None else _FIRESTORE_DB
    doc_ref = (
        firestore_db.collection("users")
        .document(user_id)
        .collection("integrations")
        .document(integration)
    )
    doc = doc_ref.get()
    if doc.exists:
        return doc.to_dict().get("tokenData", {})
    return {}


def store_integration_token(
    user_id: str,
    access_token: str,
    refresh_token: str,
    expires_in: int,
    integration: str,
    *,
    db: Any = None,
    **kwargs,
) -> None:
    # Store tokenData for a user's named accounting integration.
    firestore_db = db if db is not None else _FIRESTORE_DB
    token_data = {
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "tokenType": "Bearer",
        "expiresAt": (
            datetime.utcnow() + timedelta(seconds=expires_in)
        ).isoformat() + "Z",
        **kwargs,
    }
    doc_ref = (
        firestore_db.collection("users")
        .document(user_id)
        .collection("integrations")
        .document(integration)
    )
    doc_ref.set({"tokenData": token_data}, merge=True)


class IntegrationTokenRepository:
    def __init__(
        self,
        user_id: str,
        integration: str,
        *,
        db: Any = None,
    ):
        if not user_id:
            raise ValueError("user_id is required.")
        if not integration:
            raise ValueError("integration is required.")

        self.user_id = user_id
        self.integration = integration.lower()
        self.db = db if db is not None else _FIRESTORE_DB

    def get_integration_token(self) -> dict:
        return get_integration_token(
            self.user_id,
            self.integration,
            db=self.db,
        )

    def store_integration_token(
        self,
        access_token: str,
        refresh_token: str,
        expires_in: int,
        **kwargs,
    ) -> None:
        store_integration_token(
            self.user_id,
            access_token,
            refresh_token,
            expires_in,
            self.integration,
            db=self.db,
            **kwargs,
        )
        logger.info(
            f"Stored integration token for integration: {self.integration}"
        )

    @staticmethod
    def store_integration_token_statically(
        user_id: str,
        access_token: str,
        refresh_token: str,
        expires_in: int,
        integration: str,
        **kwargs,
    ) -> None:
        store_integration_token(
            user_id,
            access_token,
            refresh_token,
            expires_in,
            integration,
            **kwargs,
        )
