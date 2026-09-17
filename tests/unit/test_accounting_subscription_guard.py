from __future__ import annotations

import asyncio
import pytest
from fastapi import HTTPException
from masyg_extractor.integrations.accounting.shared import subscription_guard as guard

class FakeSnapshot:
    def __init__(self, *, exists: bool, data=None):
        self.exists = exists
        self._data = data or {}
    def to_dict(self):
        return dict(self._data)

class FakeDoc:
    def __init__(self, user_id: str):
        self.user_id = user_id

class FakeCollection:
    def document(self, user_id: str):
        return FakeDoc(user_id)

class FakeClient:
    def collection(self, name: str):
        assert name == "users"
        return FakeCollection()

def install_snapshot(monkeypatch, snapshot):
    async def fake_client():
        return FakeClient()
    async def fake_get(doc):
        assert doc.user_id == "user-1"
        return snapshot
    monkeypatch.setattr(guard, "get_firestore_client", fake_client)
    monkeypatch.setattr(guard, "document_get", fake_get)

def run_guard(user=None):
    return asyncio.run(
        guard.require_active_accounting_subscription(
            {"userId": "user-1"} if user is None else user
        )
    )

def test_active_server_subscription_allows_mutation(monkeypatch):
    install_snapshot(monkeypatch, FakeSnapshot(exists=True, data={"isSubscribed": True}))
    assert run_guard() == {"userId": "user-1"}

@pytest.mark.parametrize("data", ({}, {"isSubscribed": False}, {"isSubscribed": None}, {"isSubscribed": "true"}))
def test_inactive_or_invalid_entitlement_is_payment_required(monkeypatch, data):
    install_snapshot(monkeypatch, FakeSnapshot(exists=True, data=data))
    with pytest.raises(HTTPException) as error:
        run_guard()
    assert error.value.status_code == 402
    assert error.value.detail["code"] == "SUBSCRIPTION_REQUIRED"

def test_missing_user_document_fails_closed(monkeypatch):
    install_snapshot(monkeypatch, FakeSnapshot(exists=False))
    with pytest.raises(HTTPException) as error:
        run_guard()
    assert error.value.status_code == 402

def test_entitlement_lookup_failure_fails_closed(monkeypatch):
    async def failing_client():
        raise RuntimeError("firestore unavailable")
    monkeypatch.setattr(guard, "get_firestore_client", failing_client)
    with pytest.raises(HTTPException) as error:
        run_guard()
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "SUBSCRIPTION_STATUS_UNAVAILABLE"

def test_missing_authenticated_identity_fails_before_firestore():
    with pytest.raises(HTTPException) as error:
        run_guard({})
    assert error.value.status_code == 401
