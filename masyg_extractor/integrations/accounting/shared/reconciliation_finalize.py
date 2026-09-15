from __future__ import annotations

import asyncio
from typing import Any

from masyg_extractor.integrations.accounting.shared.reconciliation import (
    ProviderLookupEvidence,
    resolve_provider_reconciliation_spec,
)


async def apply_found_reconciliation(
    *,
    provider: str,
    intent: str,
    record_type: str,
    group_id: str,
    transaction_id: str,
    evidence: ProviderLookupEvidence,
    repo: Any,
) -> bool:
    """
    Apply positive provider reconciliation evidence only to the exact
    durable claim generation observed immediately before provider GET.

    ABSENT and INDETERMINATE always return False without mutation.

    A FOUND result without an observed claim generation is also
    fail-closed.
    """

    if evidence.outcome != "found":
        return False

    observed_claim_token = str(
        evidence.observed_claim_token or ""
    ).strip()

    provider_document_id = str(
        evidence.provider_document_id or ""
    ).strip()

    provider_document_number = str(
        evidence.provider_document_number or ""
    ).strip()

    if not observed_claim_token:
        return False

    if not provider_document_id:
        return False

    if not provider_document_number:
        return False

    try:
        resolve_provider_reconciliation_spec(
            provider,
            intent,
        )
    except Exception:
        return False

    try:
        return bool(
            await asyncio.to_thread(
                repo.finalize_reconciled_record,
                record_type,
                group_id,
                transaction_id,
                claim_token=(
                    observed_claim_token
                ),
                provider=provider,
                intent=intent,
                provider_document_id=(
                    provider_document_id
                ),
                provider_document_number=(
                    provider_document_number
                ),
            )
        )
    except Exception:
        # A persistence fault or ownership loss must never be converted
        # into claim release or retry permission.
        return False
