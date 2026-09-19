from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import stripe
from starlette.concurrency import run_in_threadpool


def _customer_is_deleted(customer: Any) -> bool:
    if hasattr(customer, "get"):
        try:
            return customer.get("deleted") is True
        except Exception:
            pass
    return getattr(customer, "deleted", False) is True


def _is_missing_customer_error(exc: BaseException) -> bool:
    if not isinstance(exc, stripe.error.InvalidRequestError):
        return False

    code = str(getattr(exc, "code", "") or "").strip().lower()
    param = str(getattr(exc, "param", "") or "").strip().lower()
    message = str(exc).lower()

    return (
        "no such customer" in message
        or (
            code == "resource_missing"
            and param == "customer"
        )
    )


async def ensure_stripe_customer_id(
    *,
    existing_customer_id: str | None,
    email: str | None,
    name: str | None,
    persist_customer_id: Callable[[str], Awaitable[None]],
) -> str:
    """
    Resolve one usable Stripe Customer for a MASYG user.

    Reuse a live Customer. Replace only a missing/deleted Customer.
    Propagate unrelated Stripe/provider errors unchanged.
    """
    customer_id = str(existing_customer_id or "").strip()

    if customer_id:
        try:
            customer = await run_in_threadpool(
                lambda: stripe.Customer.retrieve(customer_id)
            )
        except stripe.error.InvalidRequestError as exc:
            if not _is_missing_customer_error(exc):
                raise
        else:
            if not _customer_is_deleted(customer):
                return customer_id

    def blocking_create_customer():
        payload: dict[str, Any] = {}
        if email:
            payload["email"] = email
        if name:
            payload["name"] = name
        return stripe.Customer.create(**payload)

    customer = await run_in_threadpool(blocking_create_customer)
    replacement_id = str(
        getattr(customer, "id", None)
        or (
            customer.get("id")
            if hasattr(customer, "get")
            else ""
        )
        or ""
    ).strip()

    if not replacement_id:
        raise RuntimeError("Stripe customer creation returned no customer id")

    await persist_customer_id(replacement_id)
    return replacement_id
