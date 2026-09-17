from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from firebase_admin import firestore

from masyg_extractor.integrations.accounting.shared.integration_secret_crypto import (
    seal_token_data,
    unseal_token_data,
)
from masyg_extractor.services.my_log import logger


_FIRESTORE_DB = firestore.client()


def _integration_document(
    user_id: str,
    integration: str,
    *,
    db: Any = None,
):
    firestore_db = db if db is not None else _FIRESTORE_DB
    return (
        firestore_db.collection("users")
        .document(user_id)
        .collection("integrations")
        .document(integration)
    )


def get_integration_token(
    user_id: str,
    integration: str,
    *,
    db: Any = None,
) -> dict:
    # Runtime callers keep the historical plaintext token dictionary, but
    # Firestore stores only encrypted provider credentials.
    firestore_db = db if db is not None else _FIRESTORE_DB
    doc_ref = (
        firestore_db.collection("users")
        .document(user_id)
        .collection("integrations")
        .document(integration)
    )
    doc = doc_ref.get()
    if not doc.exists:
        return {}

    document_data = doc.to_dict() or {}
    token_data = document_data.get("tokenData", {})
    if not isinstance(token_data, dict):
        return {}

    runtime_token_data, _migration_candidate = unseal_token_data(
        user_id=user_id,
        integration=integration,
        token_data=token_data,
    )

    return runtime_token_data


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
    token_data = {
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "tokenType": "Bearer",
        "expiresAt": (
            datetime.utcnow()
            + timedelta(seconds=expires_in)
        ).isoformat()
        + "Z",
        **kwargs,
    }

    stored_token_data = seal_token_data(
        user_id=user_id,
        integration=integration,
        token_data=token_data,
    )

    doc_ref = _integration_document(
        user_id,
        integration,
        db=db,
    )
    existing = doc_ref.get()
    if existing.exists:
        # Updating the top-level tokenData field replaces the complete map,
        # so legacy plaintext secret children cannot survive beside the
        # encrypted envelope.
        doc_ref.update(
            {"tokenData": stored_token_data}
        )
    else:
        # First-time connection: create the integration document without
        # disturbing any future sibling fields.
        doc_ref.set(
            {"tokenData": stored_token_data},
            merge=True,
        )


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
