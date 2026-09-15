from __future__ import annotations

import asyncio
from typing import Any

from masyg_extractor.integrations.accounting.shared.reconciliation import (
    ProviderLookupEvidence,
    build_provider_lookup_request,
    classify_provider_lookup_response,
    reconciliation_document_number,
)


def indeterminate_lookup_evidence(
    *,
    observed_claim_token: str | None = None,
) -> ProviderLookupEvidence:
    """
    Fail-closed result for any state that cannot be verified safely.

    ``observed_claim_token`` is private reconciliation ownership
    evidence. It is never part of the public durable-status surface.
    """

    return ProviderLookupEvidence(
        outcome="indeterminate",
        observed_claim_token=observed_claim_token,
    )


async def execute_provider_reconciliation_lookup(
    *,
    provider: str,
    intent: str,
    record_type: str,
    group_id: str,
    transaction_id: str,
    repo: Any,
    client: Any,
    now: Any = None,
) -> ProviderLookupEvidence:
    """
    Execute one generation-bound read-only provider reconciliation
    lookup.

    The durable record read immediately before the provider GET owns
    the reconciliation generation. The exact ``claimToken`` observed
    on that read is carried privately with provider evidence and must
    still match during FOUND finalization.

    This function never finalizes, releases, creates, retries, or
    otherwise changes the accounting claim.
    """

    from masyg_extractor.integrations.accounting.shared.durable_status import (
        _sending_recovery_metadata,
    )

    try:
        durable_record = await asyncio.to_thread(
            repo.get_record,
            record_type,
            group_id,
            transaction_id,
        )
    except Exception:
        return indeterminate_lookup_evidence()

    if not isinstance(
        durable_record,
        dict,
    ):
        return indeterminate_lookup_evidence()

    observed_claim_token = str(
        durable_record.get("claimToken")
        or ""
    ).strip()

    # Tokenless historical claims cannot be bound to a unique durable
    # generation and therefore cannot safely authorize provider
    # verification.
    if not observed_claim_token:
        return indeterminate_lookup_evidence()

    current_status = str(
        durable_record.get("status")
        or ""
    ).strip().lower()

    if current_status == "sending":
        claimed_at_raw = durable_record.get(
            "claimedAt"
        )

        claimed_at = (
            str(claimed_at_raw).strip()
            if claimed_at_raw is not None
            else None
        )

        if claimed_at == "":
            claimed_at = None

        (
            recovery_required,
            _recovery_reason,
        ) = _sending_recovery_metadata(
            status=current_status,
            claimed_at=claimed_at,
            now=now,
        )

        if recovery_required is not True:
            return indeterminate_lookup_evidence(
                observed_claim_token=(
                    observed_claim_token
                ),
            )

    elif current_status != "uncertain":
        return indeterminate_lookup_evidence(
            observed_claim_token=(
                observed_claim_token
            ),
        )

    document_number = reconciliation_document_number(
        durable_record
    )

    if not document_number:
        return indeterminate_lookup_evidence(
            observed_claim_token=(
                observed_claim_token
            ),
        )

    try:
        lookup_request = build_provider_lookup_request(
            provider,
            intent,
            document_number,
        )
    except Exception:
        return indeterminate_lookup_evidence(
            observed_claim_token=(
                observed_claim_token
            ),
        )

    # Defensive invariant: reconciliation lookups are reads only.
    if lookup_request.method != "GET":
        return indeterminate_lookup_evidence(
            observed_claim_token=(
                observed_claim_token
            ),
        )

    try:
        integration_token = await asyncio.to_thread(
            repo.get_integration_token
        )

        response = await client.request(
            integration_token,
            lookup_request.endpoint,
            method=lookup_request.method,
            params=lookup_request.params,
        )
    except Exception:
        return indeterminate_lookup_evidence(
            observed_claim_token=(
                observed_claim_token
            ),
        )

    try:
        classified = classify_provider_lookup_response(
            provider,
            intent,
            document_number,
            response,
        )
    except Exception:
        return indeterminate_lookup_evidence(
            observed_claim_token=(
                observed_claim_token
            ),
        )

    return ProviderLookupEvidence(
        outcome=classified.outcome,
        provider_document_id=(
            classified.provider_document_id
        ),
        provider_document_number=(
            classified.provider_document_number
        ),
        observed_claim_token=(
            observed_claim_token
        ),
    )
