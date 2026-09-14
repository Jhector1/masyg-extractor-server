from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import uuid
from typing import Any

from firebase_admin import firestore
from google.api_core.exceptions import AlreadyExists
from google.cloud.firestore_v1 import FieldFilter


DEFAULT_CLAIM_LEASE_SECONDS = 5 * 60
MAX_RETRY_DELAY_SECONDS = 60 * 60


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _as_utc_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    return None


def item_owner_doc_id(item_id: str) -> str:
    return hashlib.sha256(item_id.encode("utf-8")).hexdigest()


def webhook_delivery_id(raw_body: bytes, signed_jwt: str) -> str:
    """
    Stable identifier for the exact signed HTTP delivery.

    Processing correctness must not depend on this being stable across Plaid
    retries because a later delivery can legitimately be signed again.
    """
    digest = hashlib.sha256()
    digest.update(raw_body)
    digest.update(b"\0")
    digest.update(signed_jwt.encode("utf-8"))
    return digest.hexdigest()


def build_webhook_event_record(
    *,
    payload: dict[str, Any],
    claims: dict[str, Any],
    raw_body: bytes,
    user_id: str | None,
) -> dict[str, Any]:
    error = payload.get("error")
    if not isinstance(error, dict):
        error = {}

    routed = bool(user_id)

    return {
        "provider": "plaid",
        "webhookType": str(payload.get("webhook_type") or ""),
        "webhookCode": str(payload.get("webhook_code") or ""),
        "itemId": str(payload.get("item_id") or ""),
        "environment": str(payload.get("environment") or ""),
        "userId": user_id,
        "routingStatus": "routed" if routed else "unresolved",
        "processingStatus": "pending" if routed else "blocked",
        "errorCode": str(error.get("error_code") or "") or None,
        "initialUpdateComplete": payload.get("initial_update_complete"),
        "historicalUpdateComplete": payload.get("historical_update_complete"),
        "tokenIssuedAt": claims.get("iat"),
        "rawBodySha256": hashlib.sha256(raw_body).hexdigest(),
        "receivedAt": _utc_now_iso(),
        "routedAt": _utc_now_iso() if routed else None,
        "processedAt": None,
        "processingOutcome": None,
        "attemptCount": 0,
        "lastAttemptAt": None,
        "nextAttemptAt": None,
        "claimToken": None,
        "leaseExpiresAt": None,
        "lastError": None,
    }


class BankWebhookRepository:
    def __init__(self, *, db: Any = None) -> None:
        self.db = db if db is not None else firestore.client()
        self.owners_ref = self.db.collection("bank_item_owners")
        self.events_ref = self.db.collection("bank_webhook_events")

    def register_item_owner(self, item_id: str, user_id: str) -> None:
        if not item_id or not user_id:
            raise ValueError("item_id and user_id are required")

        self.owners_ref.document(item_owner_doc_id(item_id)).set(
            {
                "provider": "plaid",
                "itemId": item_id,
                "userId": user_id,
                "updatedAt": _utc_now_iso(),
            },
            merge=True,
        )

    def unregister_item_owner(self, item_id: str, user_id: str) -> None:
        ref = self.owners_ref.document(item_owner_doc_id(item_id))
        snap = ref.get()
        if not snap.exists:
            return

        data = snap.to_dict() or {}
        if (
            str(data.get("itemId") or "") == item_id
            and str(data.get("userId") or "") == user_id
        ):
            ref.delete()

    def resolve_item_owner(self, item_id: str) -> str | None:
        if not item_id:
            return None

        snap = self.owners_ref.document(item_owner_doc_id(item_id)).get()
        if not snap.exists:
            return None

        data = snap.to_dict() or {}
        if str(data.get("itemId") or "") != item_id:
            return None

        return str(data.get("userId") or "").strip() or None

    def backfill_item_owners(self) -> dict[str, int]:
        """
        Repair the reverse index for bank Items created before webhook routing
        existed.

        Only documents at:
          users/{userId}/integrations/bank/items/{itemId}
        are accepted from the collection-group scan.
        """
        scanned = 0
        registered = 0

        for snapshot in self.db.collection_group("items").stream():
            path = snapshot.reference.path.split("/")

            if (
                len(path) != 6
                or path[0] != "users"
                or path[2] != "integrations"
                or path[3] != "bank"
                or path[4] != "items"
            ):
                continue

            scanned += 1
            data = snapshot.to_dict() or {}
            item_id = str(data.get("itemId") or snapshot.id).strip()
            user_id = str(path[1] or "").strip()

            if not item_id or not user_id:
                continue

            self.register_item_owner(item_id, user_id)
            registered += 1

        return {
            "scanned": scanned,
            "registered": registered,
        }

    def record_verified_event(
        self,
        *,
        payload: dict[str, Any],
        claims: dict[str, Any],
        raw_body: bytes,
        signed_jwt: str,
    ) -> dict[str, Any]:
        item_id = str(payload.get("item_id") or "").strip()
        user_id = self.resolve_item_owner(item_id)

        event_id = webhook_delivery_id(raw_body, signed_jwt)
        record = build_webhook_event_record(
            payload=payload,
            claims=claims,
            raw_body=raw_body,
            user_id=user_id,
        )

        created = True
        try:
            self.events_ref.document(event_id).create(record)
        except AlreadyExists:
            created = False

        return {
            "event_id": event_id,
            "created": created,
            "user_id": user_id,
        }

    def list_unresolved_event_ids(self, *, limit: int = 50) -> list[str]:
        query = (
            self.events_ref
            .where(filter=FieldFilter("routingStatus", "==", "unresolved"))
            .limit(max(1, min(limit, 200)))
        )
        return [
            snapshot.id
            for snapshot in query.stream()
            if snapshot.exists
        ]

    def try_route_event(self, event_id: str) -> bool:
        ref = self.events_ref.document(event_id)
        snapshot = ref.get()

        if not snapshot.exists:
            return False

        data = snapshot.to_dict() or {}
        if str(data.get("routingStatus") or "") != "unresolved":
            return False

        item_id = str(data.get("itemId") or "").strip()
        user_id = self.resolve_item_owner(item_id)

        if not user_id:
            return False

        ref.set(
            {
                "userId": user_id,
                "routingStatus": "routed",
                "processingStatus": "pending",
                "routedAt": _utc_now_iso(),
                "nextAttemptAt": None,
            },
            merge=True,
        )
        return True

    def list_candidate_event_ids(self, *, limit: int = 50) -> list[str]:
        requested = max(1, min(limit, 200))
        ids: list[str] = []
        seen: set[str] = set()

        # Include processing rows so an expired lease can be reclaimed after
        # process death/restart.
        for processing_status in ("pending", "processing"):
            query = (
                self.events_ref
                .where(
                    filter=FieldFilter(
                        "processingStatus",
                        "==",
                        processing_status,
                    )
                )
                .limit(requested)
            )

            for snapshot in query.stream():
                if not snapshot.exists or snapshot.id in seen:
                    continue
                seen.add(snapshot.id)
                ids.append(snapshot.id)

                if len(ids) >= requested:
                    return ids

        return ids

    def claim_event(
        self,
        event_id: str,
        *,
        now: datetime | None = None,
        lease_seconds: int = DEFAULT_CLAIM_LEASE_SECONDS,
    ) -> dict[str, Any] | None:
        ref = self.events_ref.document(event_id)
        current = now or _utc_now()

        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        else:
            current = current.astimezone(timezone.utc)

        transaction = self.db.transaction()

        @firestore.transactional
        def _claim(txn):
            snapshot = ref.get(transaction=txn)
            if not snapshot.exists:
                return None

            data = snapshot.to_dict() or {}

            if str(data.get("routingStatus") or "") != "routed":
                return None

            user_id = str(data.get("userId") or "").strip()
            item_id = str(data.get("itemId") or "").strip()
            if not user_id or not item_id:
                return None

            processing_status = str(
                data.get("processingStatus") or ""
            )

            if processing_status == "processed":
                return None

            if processing_status == "processing":
                lease_expires_at = _as_utc_datetime(
                    data.get("leaseExpiresAt")
                )
                if (
                    lease_expires_at is not None
                    and lease_expires_at > current
                ):
                    return None
            elif processing_status != "pending":
                return None

            next_attempt_at = _as_utc_datetime(
                data.get("nextAttemptAt")
            )
            if (
                next_attempt_at is not None
                and next_attempt_at > current
            ):
                return None

            attempt_count = int(data.get("attemptCount") or 0) + 1
            claim_token = uuid.uuid4().hex
            lease_expires_at = current + timedelta(
                seconds=max(30, lease_seconds)
            )

            txn.update(
                ref,
                {
                    "processingStatus": "processing",
                    "attemptCount": attempt_count,
                    "lastAttemptAt": current,
                    "claimToken": claim_token,
                    "leaseExpiresAt": lease_expires_at,
                    "lastError": None,
                },
            )

            claimed = dict(data)
            claimed.update(
                {
                    "eventId": event_id,
                    "processingStatus": "processing",
                    "attemptCount": attempt_count,
                    "claimToken": claim_token,
                    "lastAttemptAt": current,
                    "leaseExpiresAt": lease_expires_at,
                }
            )
            return claimed

        return _claim(transaction)

    def mark_processed(
        self,
        event_id: str,
        claim_token: str,
        *,
        outcome: str,
    ) -> bool:
        ref = self.events_ref.document(event_id)
        transaction = self.db.transaction()

        @firestore.transactional
        def _complete(txn) -> bool:
            snapshot = ref.get(transaction=txn)
            if not snapshot.exists:
                return False

            data = snapshot.to_dict() or {}

            if (
                str(data.get("processingStatus") or "") != "processing"
                or str(data.get("claimToken") or "") != claim_token
            ):
                return False

            txn.update(
                ref,
                {
                    "processingStatus": "processed",
                    "processingOutcome": outcome,
                    "processedAt": _utc_now(),
                    "claimToken": None,
                    "leaseExpiresAt": None,
                    "nextAttemptAt": None,
                    "lastError": None,
                },
            )
            return True

        return _complete(transaction)

    def mark_retry(
        self,
        event_id: str,
        claim_token: str,
        *,
        attempt_count: int,
        error: str,
    ) -> bool:
        ref = self.events_ref.document(event_id)
        transaction = self.db.transaction()

        @firestore.transactional
        def _retry(txn) -> bool:
            snapshot = ref.get(transaction=txn)
            if not snapshot.exists:
                return False

            data = snapshot.to_dict() or {}

            if (
                str(data.get("processingStatus") or "") != "processing"
                or str(data.get("claimToken") or "") != claim_token
            ):
                return False

            delay_seconds = min(
                60 * (2 ** max(0, attempt_count - 1)),
                MAX_RETRY_DELAY_SECONDS,
            )
            now = _utc_now()

            txn.update(
                ref,
                {
                    "processingStatus": "pending",
                    "processingOutcome": None,
                    "claimToken": None,
                    "leaseExpiresAt": None,
                    "nextAttemptAt": now
                    + timedelta(seconds=delay_seconds),
                    "lastError": str(error or "")[:500],
                },
            )
            return True

        return _retry(transaction)
