from __future__ import annotations

from firebase_admin import firestore

from masyg_extractor.integrations.accounting.shared.token_repository import (
    IntegrationTokenRepository,
)


GOOGLE_DRIVE_INTEGRATION_ID = "google_drive"


class GoogleDriveCredentialRepository:
    """
    Thin Drive owner over MASYG's canonical encrypted integration-token repository.
    No second token encryption format is introduced here.
    """

    def __init__(self, user_id: str):
        normalized = str(user_id or "").strip()
        if not normalized:
            raise ValueError("user_id is required")

        self.user_id = normalized
        self.tokens = IntegrationTokenRepository(
            self.user_id,
            GOOGLE_DRIVE_INTEGRATION_ID,
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

    def is_connected(self) -> bool:
        return bool(self.access_token() and self.refresh_token())

    def store_token(
        self,
        *,
        access_token: str,
        refresh_token: str,
        expires_in: int,
        scope: str = "",
        token_type: str = "Bearer",
    ) -> None:
        # scope/token_type are accepted at the provider boundary, but secret
        # persistence deliberately reuses the existing canonical repository shape.
        del scope, token_type
        self.tokens.store_integration_token(
            access_token,
            refresh_token,
            expires_in,
        )

    def disconnect(self) -> None:
        (
            firestore.client()
            .collection("users")
            .document(self.user_id)
            .collection("integrations")
            .document(GOOGLE_DRIVE_INTEGRATION_ID)
            .delete()
        )
