from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from masyg_extractor.services import stripe_customer


ROOT = Path(__file__).resolve().parents[2]


def run(coro):
    return asyncio.run(coro)


def test_reuses_valid_stored_customer_without_creating_or_persisting(monkeypatch):
    calls = {"create": 0, "persist": []}

    monkeypatch.setattr(
        stripe_customer.stripe.Customer,
        "retrieve",
        lambda customer_id: {"id": customer_id, "deleted": False},
    )

    def unexpected_create(**kwargs):
        calls["create"] += 1
        raise AssertionError("valid customer must not be recreated")

    monkeypatch.setattr(
        stripe_customer.stripe.Customer,
        "create",
        unexpected_create,
    )

    async def persist(customer_id):
        calls["persist"].append(customer_id)

    resolved = run(
        stripe_customer.ensure_stripe_customer_id(
            existing_customer_id="cus_valid",
            email="user@example.test",
            name="User",
            persist_customer_id=persist,
        )
    )

    assert resolved == "cus_valid"
    assert calls == {"create": 0, "persist": []}


def test_creates_and_persists_when_no_customer_is_stored(monkeypatch):
    created = []
    persisted = []

    def create(**kwargs):
        created.append(kwargs)
        return SimpleNamespace(id="cus_new")

    monkeypatch.setattr(stripe_customer.stripe.Customer, "create", create)

    async def persist(customer_id):
        persisted.append(customer_id)

    resolved = run(
        stripe_customer.ensure_stripe_customer_id(
            existing_customer_id=None,
            email="user@example.test",
            name="User",
            persist_customer_id=persist,
        )
    )

    assert resolved == "cus_new"
    assert created == [{"email": "user@example.test", "name": "User"}]
    assert persisted == ["cus_new"]


def test_replaces_and_persists_resource_missing_customer(monkeypatch):
    class FakeInvalidRequestError(Exception):
        code = "resource_missing"
        param = "customer"

        def __str__(self):
            return "No such customer: 'cus_stale'"

    monkeypatch.setattr(
        stripe_customer.stripe.error,
        "InvalidRequestError",
        FakeInvalidRequestError,
    )

    def retrieve(_customer_id):
        raise FakeInvalidRequestError()

    monkeypatch.setattr(stripe_customer.stripe.Customer, "retrieve", retrieve)
    monkeypatch.setattr(
        stripe_customer.stripe.Customer,
        "create",
        lambda **_kwargs: SimpleNamespace(id="cus_replacement"),
    )

    persisted = []

    async def persist(customer_id):
        persisted.append(customer_id)

    resolved = run(
        stripe_customer.ensure_stripe_customer_id(
            existing_customer_id="cus_stale",
            email="user@example.test",
            name="User",
            persist_customer_id=persist,
        )
    )

    assert resolved == "cus_replacement"
    assert persisted == ["cus_replacement"]


def test_replaces_deleted_customer(monkeypatch):
    monkeypatch.setattr(
        stripe_customer.stripe.Customer,
        "retrieve",
        lambda _customer_id: {"id": "cus_deleted", "deleted": True},
    )
    monkeypatch.setattr(
        stripe_customer.stripe.Customer,
        "create",
        lambda **_kwargs: SimpleNamespace(id="cus_after_delete"),
    )

    persisted = []

    async def persist(customer_id):
        persisted.append(customer_id)

    resolved = run(
        stripe_customer.ensure_stripe_customer_id(
            existing_customer_id="cus_deleted",
            email="user@example.test",
            name="User",
            persist_customer_id=persist,
        )
    )

    assert resolved == "cus_after_delete"
    assert persisted == ["cus_after_delete"]


def test_unrelated_invalid_request_error_is_not_swallowed(monkeypatch):
    class FakeInvalidRequestError(Exception):
        code = "parameter_invalid_integer"
        param = "limit"

        def __str__(self):
            return "Invalid limit"

    monkeypatch.setattr(
        stripe_customer.stripe.error,
        "InvalidRequestError",
        FakeInvalidRequestError,
    )

    def retrieve(_customer_id):
        raise FakeInvalidRequestError()

    monkeypatch.setattr(stripe_customer.stripe.Customer, "retrieve", retrieve)

    async def persist(_customer_id):
        raise AssertionError("must not persist after unrelated Stripe failure")

    with pytest.raises(FakeInvalidRequestError):
        run(
            stripe_customer.ensure_stripe_customer_id(
                existing_customer_id="cus_valid_shape",
                email="user@example.test",
                name="User",
                persist_customer_id=persist,
            )
        )


def test_checkout_uses_canonical_customer_recovery_before_session_creation():
    source = (ROOT / "masyg_extractor/routes/payment_routes.py").read_text()

    start = source.index("async def create_checkout_session(")
    end = source.index(
        '@router.post("/customer-portal")',
        start,
    )
    block = source[start:end]

    assert (
        "from masyg_extractor.services.stripe_customer import "
        "ensure_stripe_customer_id"
        in source
    )
    assert "await ensure_stripe_customer_id(" in block
    assert 'existing_customer_id=user_data.get("stripeCustomerId")' in block
    assert '{"stripeCustomerId": customer_id}' in block
    assert "customer=stripe_customer_id" in block
    assert "if not stripe_customer_id:" not in block
