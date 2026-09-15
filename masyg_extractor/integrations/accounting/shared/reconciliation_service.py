from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from masyg_extractor.integrations.accounting.shared.durable_status import (
    read_accounting_durable_status,
)
from masyg_extractor.integrations.accounting.shared.reconciliation import (
    ReconciliationOutcome,
)
from masyg_extractor.integrations.accounting.shared.reconciliation_finalize import (
    apply_found_reconciliation,
)
from masyg_extractor.integrations.accounting.shared.reconciliation_lookup import (
    execute_provider_reconciliation_lookup,
)


VerifyDisposition = Literal[
    "not_eligible",
    "processing",
    "needs_verification",
    "already_created",
]


@dataclass(frozen=True)
class VerifyAccountingStatusResult:
    """
    Internal result for explicit accounting status verification.

    This owner does not expose an HTTP route. It coordinates existing
    durable-status, provider-read, and FOUND-only mutation owners.
    """

    disposition: VerifyDisposition
    lookup_outcome: ReconciliationOutcome | None
    reconciled: bool
    durable_status: dict[str, Any] | None


async def _read_status(
    *,
    repo: Any,
    provider: str,
    intent: str,
    group_id: str,
    file_id: str,
    now: datetime | None,
) -> dict[str, Any]:
    return await asyncio.to_thread(
        read_accounting_durable_status,
        repo,
        provider=provider,
        intent=intent,
        group_id=group_id,
        file_id=file_id,
        now=now,
    )


def _eligible_for_provider_verification(
    durable_status: dict[str, Any],
) -> bool:
    status = str(
        durable_status.get("status") or ""
    ).strip().lower()

    if status == "uncertain":
        return True

    if status == "sending":
        return (
            durable_status.get("recovery_required")
            is True
        )

    return False


def _result(
    *,
    disposition: VerifyDisposition,
    durable_status: dict[str, Any] | None,
    lookup_outcome: ReconciliationOutcome | None = None,
    reconciled: bool = False,
) -> VerifyAccountingStatusResult:
    return VerifyAccountingStatusResult(
        disposition=disposition,
        lookup_outcome=lookup_outcome,
        reconciled=reconciled,
        durable_status=durable_status,
    )


async def verify_accounting_status(
    *,
    provider: str,
    intent: str,
    group_id: str,
    file_id: str,
    repo: Any,
    client: Any,
    now: datetime | None = None,
) -> VerifyAccountingStatusResult:
    """
    Explicitly verify one duplicate-blocked accounting operation.

    Eligibility is owned by canonical durable status:

    - ``uncertain`` is eligible
    - ``sending`` is eligible only when recovery_required is true
    - recent ``sending`` remains Processing and never calls provider
    - ``none`` and ``succeeded`` never call provider

    Provider reconciliation is positive-proof only:

    - FOUND may atomically reconcile the existing claim to succeeded
    - ABSENT performs no mutation
    - INDETERMINATE performs no mutation

    The operation never releases a claim, retries provider creation,
    or performs provider writes.
    """

    provider_name = str(
        provider or ""
    ).strip().lower()

    accounting_intent = str(
        intent or ""
    ).strip()

    group = str(
        group_id or ""
    ).strip()

    file_identity = str(
        file_id or ""
    ).strip()

    if (
        not provider_name
        or not accounting_intent
        or not group
        or not file_identity
    ):
        return _result(
            disposition="not_eligible",
            durable_status=None,
        )

    try:
        initial_status = await _read_status(
            repo=repo,
            provider=provider_name,
            intent=accounting_intent,
            group_id=group,
            file_id=file_identity,
            now=now,
        )
    except Exception:
        return _result(
            disposition="not_eligible",
            durable_status=None,
        )

    current_status = str(
        initial_status.get("status") or ""
    ).strip().lower()

    if current_status == "succeeded":
        return _result(
            disposition="already_created",
            durable_status=initial_status,
        )

    if (
        current_status == "sending"
        and initial_status.get(
            "recovery_required"
        )
        is not True
    ):
        return _result(
            disposition="processing",
            durable_status=initial_status,
        )

    if not _eligible_for_provider_verification(
        initial_status
    ):
        return _result(
            disposition="not_eligible",
            durable_status=initial_status,
        )

    # Historical Xero aliases preserve read compatibility, but the
    # public durable status intentionally exposes the canonical file
    # identity rather than the physical legacy transaction ID. Do not
    # mutate a legacy path until that storage identity is explicit.
    if initial_status.get("legacy") is True:
        return _result(
            disposition="needs_verification",
            durable_status=initial_status,
            lookup_outcome="indeterminate",
        )

    record_type = str(
        initial_status.get("record_type") or ""
    ).strip()

    durable_number = str(
        initial_status.get(
            "provider_document_number"
        )
        or ""
    ).strip()

    if not record_type or not durable_number:
        return _result(
            disposition="needs_verification",
            durable_status=initial_status,
            lookup_outcome="indeterminate",
        )

    evidence = (
        await execute_provider_reconciliation_lookup(
            provider=provider_name,
            intent=accounting_intent,
            record_type=record_type,
            group_id=group,
            transaction_id=file_identity,
            repo=repo,
            client=client,
            now=now,
        )
    )

    if evidence.outcome != "found":
        return _result(
            disposition="needs_verification",
            durable_status=initial_status,
            lookup_outcome=evidence.outcome,
        )

    reconciled = await apply_found_reconciliation(
        provider=provider_name,
        intent=accounting_intent,
        record_type=record_type,
        group_id=group,
        transaction_id=file_identity,
        evidence=evidence,
        repo=repo,
    )

    # Never report success merely because the provider lookup returned
    # FOUND or the mutation owner returned True. Canonical durable state
    # is the final authority exposed to callers.
    try:
        final_status = await _read_status(
            repo=repo,
            provider=provider_name,
            intent=accounting_intent,
            group_id=group,
            file_id=file_identity,
            now=now,
        )
    except Exception:
        return _result(
            disposition="needs_verification",
            durable_status=initial_status,
            lookup_outcome="found",
            reconciled=reconciled,
        )

    if (
        str(
            final_status.get("status") or ""
        ).strip().lower()
        == "succeeded"
    ):
        return _result(
            disposition="already_created",
            durable_status=final_status,
            lookup_outcome="found",
            reconciled=reconciled,
        )

    return _result(
        disposition="needs_verification",
        durable_status=final_status,
        lookup_outcome="found",
        reconciled=reconciled,
    )
