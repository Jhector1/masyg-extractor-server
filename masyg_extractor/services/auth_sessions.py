"""Server-side refresh-session rotation and revocation.

Refresh JWTs are bearer credentials. Storing only a hash of the current refresh JTI lets
us reject replayed/rotated tokens and revoke a browser session at logout without storing
raw refresh tokens in Firestore.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from firebase_admin import firestore

from masyg_extractor.security import hash_refresh_jti, refresh_session_matches

_SESSION_COLLECTION = "authSessions"


def _session_ref(user_id: str, session_id: str):
    return (
        firestore.client()
        .collection("users")
        .document(user_id)
        .collection(_SESSION_COLLECTION)
        .document(session_id)
    )


async def create_refresh_session(
    user_id: str,
    session_id: str,
    refresh_jti: str,
    *,
    expires_at: datetime,
) -> None:
    now = datetime.now(timezone.utc)
    payload = {
        "refreshJtiHash": hash_refresh_jti(refresh_jti),
        "createdAt": now,
        "rotatedAt": now,
        "expiresAt": expires_at,
    }
    await asyncio.to_thread(_session_ref(user_id, session_id).set, payload)


async def rotate_refresh_session(
    user_id: str,
    session_id: str,
    presented_jti: str,
    new_jti: str,
    *,
    expires_at: datetime,
) -> bool:
    def _rotate() -> bool:
        db = firestore.client()
        ref = (
            db.collection("users")
            .document(user_id)
            .collection(_SESSION_COLLECTION)
            .document(session_id)
        )
        transaction = db.transaction()

        @firestore.transactional
        def _transactional_rotate(txn) -> bool:
            snapshot = ref.get(transaction=txn)
            if not snapshot.exists:
                return False
            data = snapshot.to_dict() or {}
            if not refresh_session_matches(data, presented_jti):
                return False

            txn.update(ref, {
                "refreshJtiHash": hash_refresh_jti(new_jti),
                "rotatedAt": datetime.now(timezone.utc),
                "expiresAt": expires_at,
            })
            return True

        return _transactional_rotate(transaction)

    return await asyncio.to_thread(_rotate)


async def revoke_refresh_session(user_id: str, session_id: str) -> None:
    # Delete instead of retaining revoked credentials indefinitely.
    await asyncio.to_thread(_session_ref(user_id, session_id).delete)


async def revoke_all_refresh_sessions(user_id: str) -> None:
    def _delete_all() -> None:
        sessions = (
            firestore.client()
            .collection("users")
            .document(user_id)
            .collection(_SESSION_COLLECTION)
        )
        docs = list(sessions.stream())
        for doc in docs:
            doc.reference.delete()

    await asyncio.to_thread(_delete_all)
