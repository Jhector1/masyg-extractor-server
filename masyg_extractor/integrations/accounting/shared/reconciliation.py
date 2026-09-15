from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ReconciliationOutcome = Literal[
    "found",
    "absent",
    "indeterminate",
]


@dataclass(frozen=True)
class ProviderReconciliationSpec:
    """
    Provider-specific identity required to verify whether an
    accounting document already exists.

    This module describes lookup semantics only. It deliberately
    performs no provider I/O and never grants retry permission.
    """

    provider: str
    intent: str
    provider_resource: str
    provider_number_field: str
    provider_type_field: str | None = None
    provider_type_value: str | None = None
    absence_can_unlock: bool = False


@dataclass(frozen=True)
class ReconciliationDecision:
    """
    Safety decision after a provider verification attempt.

    Only a positive provider match may finalize the canonical
    accounting record. Neither absence nor an indeterminate result
    releases the durable duplicate barrier.
    """

    outcome: ReconciliationOutcome
    finalize_succeeded: bool
    release_claim: bool
    allow_create: bool
    display_state: str


_PROVIDER_RECONCILIATION_SPECS: dict[
    tuple[str, str],
    ProviderReconciliationSpec,
] = {
    (
        "quickbooks",
        "create_ar_invoice",
    ): ProviderReconciliationSpec(
        provider="quickbooks",
        intent="create_ar_invoice",
        provider_resource="Invoice",
        provider_number_field="DocNumber",
    ),
    (
        "quickbooks",
        "create_sales_receipt",
    ): ProviderReconciliationSpec(
        provider="quickbooks",
        intent="create_sales_receipt",
        provider_resource="SalesReceipt",
        provider_number_field="DocNumber",
    ),
    (
        "xero",
        "create_ar_invoice",
    ): ProviderReconciliationSpec(
        provider="xero",
        intent="create_ar_invoice",
        provider_resource="Invoices",
        provider_number_field="InvoiceNumber",
        provider_type_field="Type",
        provider_type_value="ACCREC",
    ),
    (
        "xero",
        "create_ap_bill",
    ): ProviderReconciliationSpec(
        provider="xero",
        intent="create_ap_bill",
        provider_resource="Invoices",
        provider_number_field="InvoiceNumber",
        provider_type_field="Type",
        provider_type_value="ACCPAY",
    ),
}


def resolve_provider_reconciliation_spec(
    provider: str,
    intent: str,
) -> ProviderReconciliationSpec:
    key = (
        str(provider or "").strip().lower(),
        str(intent or "").strip().lower(),
    )

    try:
        return _PROVIDER_RECONCILIATION_SPECS[key]
    except KeyError as exc:
        raise ValueError(
            "Unsupported accounting provider/intent "
            "for reconciliation"
        ) from exc


def reconciliation_document_number(
    durable_record: dict | None,
) -> str | None:
    """
    Return a durable provider correlation number only for a record
    whose outcome still requires verification.

    Dispatch code persists providerDocumentNumber before the
    accounting-document provider request. ``docNumber`` remains
    accepted as a compatibility fallback when present on older
    partially-enriched records.

    A missing number means provider verification is not safely
    addressable and must remain blocked.
    """

    if not isinstance(durable_record, dict):
        return None

    status = str(
        durable_record.get("status") or ""
    ).strip().lower()

    if status not in {
        "sending",
        "uncertain",
    }:
        return None

    for field in (
        "providerDocumentNumber",
        "docNumber",
    ):
        value = str(
            durable_record.get(field) or ""
        ).strip()

        if value:
            return value

    return None



@dataclass(frozen=True)
class ProviderLookupRequest:
    """
    Pure description of the read-only provider lookup required for
    accounting recovery.

    Building this object performs no provider I/O.
    """

    provider: str
    endpoint: str
    method: str
    params: dict[str, str]


@dataclass(frozen=True)
class ProviderLookupEvidence:
    """
    Provider evidence after a read-only reconciliation lookup.

    ``found`` is positive proof only when the provider object has the
    exact expected correlation number, resource/type semantics, and
    stable provider ID.

    ``absent`` never grants retry permission.
    """

    outcome: ReconciliationOutcome
    provider_document_id: str | None = None
    provider_document_number: str | None = None
    observed_claim_token: str | None = None


def _required_document_number(
    document_number: str,
) -> str:
    number = str(
        document_number or ""
    ).strip()

    if not number:
        raise ValueError(
            "provider document number is required"
        )

    return number


def _quickbooks_sql_literal(
    value: str,
) -> str:
    """
    Match the repository's existing QBO SQL literal escaping.
    """

    return value.replace("'", "''")


def _xero_where_literal(
    value: str,
) -> str:
    """
    Recovery numbers generated by this application do not contain
    Xero where-expression quote syntax.

    Fail closed for an unsupported historical value rather than
    constructing a lookup whose exact-match semantics are uncertain.
    """

    if '"' in value or "\\" in value:
        raise ValueError(
            "provider document number cannot be represented "
            "safely in the Xero lookup"
        )

    return value


def build_provider_lookup_request(
    provider: str,
    intent: str,
    document_number: str,
) -> ProviderLookupRequest:
    """
    Build the exact read-only provider lookup.

    QuickBooks uses the existing QBO query endpoint and limits the
    response to two records so reconciliation can detect a duplicate
    correlation number.

    Xero uses the existing GET/where adapter contract for InvoiceNumber.
    Type is verified locally from the returned object rather than
    assuming unproven compound where-expression syntax.
    """

    spec = resolve_provider_reconciliation_spec(
        provider,
        intent,
    )

    number = _required_document_number(
        document_number
    )

    if spec.provider == "quickbooks":
        escaped = _quickbooks_sql_literal(
            number
        )

        query = (
            f"SELECT Id, {spec.provider_number_field} "
            f"FROM {spec.provider_resource} "
            f"WHERE {spec.provider_number_field} = "
            f"'{escaped}' "
            "STARTPOSITION 1 MAXRESULTS 2"
        )

        return ProviderLookupRequest(
            provider="quickbooks",
            endpoint="query",
            method="GET",
            params={
                "query": query,
            },
        )

    if spec.provider == "xero":
        escaped = _xero_where_literal(
            number
        )

        where = (
            f'{spec.provider_number_field}'
            f'=="{escaped}"'
        )

        return ProviderLookupRequest(
            provider="xero",
            endpoint=spec.provider_resource,
            method="GET",
            params={
                "where": where,
            },
        )

    raise ValueError(
        "Unsupported accounting provider for reconciliation lookup"
    )


def _indeterminate_lookup() -> ProviderLookupEvidence:
    return ProviderLookupEvidence(
        outcome="indeterminate",
    )


def _classify_quickbooks_lookup(
    spec: ProviderReconciliationSpec,
    document_number: str,
    response: object,
) -> ProviderLookupEvidence:
    if not isinstance(response, dict):
        return _indeterminate_lookup()

    if response.get("error"):
        return _indeterminate_lookup()

    query_response = response.get(
        "QueryResponse"
    )

    if not isinstance(
        query_response,
        dict,
    ):
        return _indeterminate_lookup()

    # ABSENT requires an explicit empty collection for the exact
    # resource queried. A structurally valid QueryResponse that omits
    # the expected resource is not sufficient negative evidence.
    if spec.provider_resource not in query_response:
        return _indeterminate_lookup()

    raw_rows = query_response.get(
        spec.provider_resource
    )

    if not isinstance(
        raw_rows,
        list,
    ):
        return _indeterminate_lookup()

    if not raw_rows:
        return ProviderLookupEvidence(
            outcome="absent",
        )

    # The query itself is exact. Any surprising cardinality or row
    # shape is not trustworthy enough to reconcile automatically.
    if len(raw_rows) != 1:
        return _indeterminate_lookup()

    row = raw_rows[0]

    if not isinstance(row, dict):
        return _indeterminate_lookup()

    returned_number = str(
        row.get(
            spec.provider_number_field
        )
        or ""
    ).strip()

    provider_id = str(
        row.get("Id")
        or ""
    ).strip()

    if returned_number != document_number:
        return _indeterminate_lookup()

    if not provider_id:
        return _indeterminate_lookup()

    return ProviderLookupEvidence(
        outcome="found",
        provider_document_id=provider_id,
        provider_document_number=returned_number,
    )


def _classify_xero_lookup(
    spec: ProviderReconciliationSpec,
    document_number: str,
    response: object,
) -> ProviderLookupEvidence:
    if not isinstance(response, dict):
        return _indeterminate_lookup()

    if response.get("error"):
        return _indeterminate_lookup()

    if spec.provider_resource not in response:
        return _indeterminate_lookup()

    raw_rows = response.get(
        spec.provider_resource
    )

    if not isinstance(
        raw_rows,
        list,
    ):
        return _indeterminate_lookup()

    if not raw_rows:
        return ProviderLookupEvidence(
            outcome="absent",
        )

    if len(raw_rows) != 1:
        return _indeterminate_lookup()

    row = raw_rows[0]

    if not isinstance(row, dict):
        return _indeterminate_lookup()

    returned_number = str(
        row.get(
            spec.provider_number_field
        )
        or ""
    ).strip()

    returned_type = str(
        row.get(
            spec.provider_type_field
        )
        or ""
    ).strip().upper()

    provider_id = str(
        row.get("InvoiceID")
        or ""
    ).strip()

    if returned_number != document_number:
        return _indeterminate_lookup()

    if (
        spec.provider_type_value
        and returned_type
        != spec.provider_type_value
    ):
        return _indeterminate_lookup()

    if not provider_id:
        return _indeterminate_lookup()

    return ProviderLookupEvidence(
        outcome="found",
        provider_document_id=provider_id,
        provider_document_number=returned_number,
    )


def classify_provider_lookup_response(
    provider: str,
    intent: str,
    document_number: str,
    response: object,
) -> ProviderLookupEvidence:
    """
    Classify read-only provider evidence.

    This function performs no provider call and no durable mutation.

    FOUND:
      exactly one validated provider object.

    ABSENT:
      structurally valid exact lookup with zero rows.

    INDETERMINATE:
      error envelope, malformed response, multiple rows, mismatched
      correlation identity/type, or missing provider ID.
    """

    spec = resolve_provider_reconciliation_spec(
        provider,
        intent,
    )

    number = _required_document_number(
        document_number
    )

    if spec.provider == "quickbooks":
        return _classify_quickbooks_lookup(
            spec,
            number,
            response,
        )

    if spec.provider == "xero":
        return _classify_xero_lookup(
            spec,
            number,
            response,
        )

    return _indeterminate_lookup()


def reconciliation_decision(
    outcome: ReconciliationOutcome,
) -> ReconciliationDecision:
    """
    Convert provider verification evidence into duplicate-barrier
    behavior.

    FOUND:
      provider existence is positive evidence; finalize the existing
      durable claim as succeeded.

    ABSENT:
      absence is not currently considered sufficient evidence for
      automatic retry. Keep the claim blocked.

    INDETERMINATE:
      auth, timeout, rate limit, provider fault, ambiguous response,
      multiple matches, or any unverifiable result remains blocked.
    """

    if outcome == "found":
        return ReconciliationDecision(
            outcome="found",
            finalize_succeeded=True,
            release_claim=False,
            allow_create=False,
            display_state="already_created",
        )

    if outcome in {
        "absent",
        "indeterminate",
    }:
        return ReconciliationDecision(
            outcome=outcome,
            finalize_succeeded=False,
            release_claim=False,
            allow_create=False,
            display_state="needs_verification",
        )

    raise ValueError(
        f"Unsupported reconciliation outcome: {outcome}"
    )
