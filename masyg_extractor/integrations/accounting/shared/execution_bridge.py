from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Request

from masyg_extractor.integrations.accounting.core.integration_context import (
    IntegrationContext,
)
from masyg_extractor.integrations.accounting.core.models import (
    Document,
)
from masyg_extractor.integrations.accounting.registry import (
    get_accounting_execution_action,
)
from masyg_extractor.services.log_manager import (
    LogManager,
)
from masyg_extractor.services.progress_log import (
    IntegrationsProgressLog,
    XeroIntegrationsProgressLog,
)


@dataclass(frozen=True)
class AccountingExecutionBridgeSpec:
    provider: str
    accounting_intent: str
    context_doc_type: str
    service_doc_type: str
    doc_number_prefix: str
    progress_log_key: str
    progress_shape: str
    invoice_status: str | None = None


_EXECUTION_BRIDGE_SPECS: dict[
    tuple[str, str],
    AccountingExecutionBridgeSpec,
] = {
    (
        "quickbooks",
        "create_ar_invoice",
    ): AccountingExecutionBridgeSpec(
        provider="quickbooks",
        accounting_intent="create_ar_invoice",
        context_doc_type="Invoice",
        service_doc_type="Invoice",
        doc_number_prefix="Inv",
        progress_log_key="quickbooks-invoice-progress",
        progress_shape="quickbooks",
    ),
    (
        "quickbooks",
        "create_sales_receipt",
    ): AccountingExecutionBridgeSpec(
        provider="quickbooks",
        accounting_intent="create_sales_receipt",
        context_doc_type="SalesReceipt",
        service_doc_type="SalesReceipt",
        doc_number_prefix="REC",
        progress_log_key="quickbooks-invoice-progress",
        progress_shape="quickbooks",
    ),
    (
        "xero",
        "create_ar_invoice",
    ): AccountingExecutionBridgeSpec(
        provider="xero",
        accounting_intent="create_ar_invoice",
        context_doc_type="Invoices",
        service_doc_type="Invoices",
        doc_number_prefix="Inv",
        progress_log_key="xero-invoice-progress",
        progress_shape="xero",
        invoice_status="ACCREC",
    ),
    (
        "xero",
        "create_ap_bill",
    ): AccountingExecutionBridgeSpec(
        provider="xero",
        accounting_intent="create_ap_bill",
        context_doc_type="Invoices",
        service_doc_type="Invoices",
        doc_number_prefix="Inv",
        progress_log_key="xero-invoice-progress",
        progress_shape="xero",
        invoice_status="ACCPAY",
    ),
}


@dataclass(frozen=True)
class AccountingExecutionRuntime:
    repo_factory: Any
    client_factory: Any
    service_factory: Any
    document_factory: Any


def _load_quickbooks_runtime() -> AccountingExecutionRuntime:
    """
    Load QuickBooks runtime dependencies only when an execution
    bridge is actually being constructed.

    The provider client imports token infrastructure that expects
    the application Firebase lifecycle to already be initialized.
    Keeping it out of module import makes the shared accounting
    execution layer safe to import in isolation.
    """

    from masyg_extractor.integrations.accounting.quickbooks.adapter import (
        QuickBooksClientAdapter,
    )
    from masyg_extractor.integrations.accounting.quickbooks.route_helper import (
        create_document,
    )
    from masyg_extractor.integrations.accounting.quickbooks.services.document_service import (
        DocumentService,
    )
    from masyg_extractor.integrations.accounting.shared.firestore_repository import (
        QuickBooksFirestoreService,
    )

    return AccountingExecutionRuntime(
        repo_factory=QuickBooksFirestoreService,
        client_factory=QuickBooksClientAdapter,
        service_factory=DocumentService,
        document_factory=create_document,
    )


def _load_xero_runtime() -> AccountingExecutionRuntime:
    """
    Load Xero runtime dependencies only when an execution bridge
    is actually being constructed.
    """

    from masyg_extractor.integrations.accounting.shared.firestore_repository import (
        QuickBooksFirestoreService,
    )
    from masyg_extractor.integrations.accounting.xero.adapter import (
        XeroClientAdapter,
    )
    from masyg_extractor.integrations.accounting.xero.route_helper import (
        create_document,
    )
    from masyg_extractor.integrations.accounting.xero.services.document_service import (
        DocumentService,
    )

    return AccountingExecutionRuntime(
        repo_factory=QuickBooksFirestoreService,
        client_factory=XeroClientAdapter,
        service_factory=DocumentService,
        document_factory=create_document,
    )


def _load_execution_runtime(
    provider: str,
) -> AccountingExecutionRuntime:
    if provider == "quickbooks":
        return _load_quickbooks_runtime()

    if provider == "xero":
        return _load_xero_runtime()

    raise ValueError(
        "Unsupported accounting execution provider."
    )


def get_accounting_execution_bridge_spec(
    provider: str,
    accounting_intent: str,
) -> AccountingExecutionBridgeSpec:
    provider = str(
        provider or ""
    ).strip().lower()

    accounting_intent = str(
        accounting_intent or ""
    ).strip()

    # A3A remains the canonical capability authority.
    get_accounting_execution_action(
        provider,
        accounting_intent,
    )

    try:
        return _EXECUTION_BRIDGE_SPECS[
            (
                provider,
                accounting_intent,
            )
        ]
    except KeyError as exc:
        raise ValueError(
            "Accounting execution capability has no "
            "provider construction bridge."
        ) from exc


@dataclass
class AccountingExecutionBridge:
    spec: AccountingExecutionBridgeSpec
    service: Any
    document_factory: Any
    progress_logger: IntegrationsProgressLog
    progress: dict[str, float]

    async def send_documents(
        self,
        documents: list[Document],
    ) -> dict[str, Any]:
        if not documents:
            raise ValueError(
                "At least one accounting document is required."
            )

        if self.spec.provider == "quickbooks":
            # Preserve the current QuickBooks bulk-route behavior.
            await self.progress_logger.safe_emit_progress(
                self.progress
            )

            share_progress = (
                IntegrationsProgressLog
                .CREATING_DOCUMENTS_WEIGHT
            )

            return await self.service.send_document_in_bulk(
                documents,
                share_progress,
            )

        if self.spec.provider == "xero":
            # Preserve the actual current Xero bulk-route runtime
            # behavior rather than changing progress semantics in
            # the execution-orchestration wave.
            overall_progress = (
                self.progress_logger
                .calculate_overall_progress(
                    self.progress
                )
            )

            share_progress = (
                await self.progress_logger
                .safe_emit_progress(
                    overall_progress
                )
            )

            return await self.service.send_document_in_bulk(
                documents,
                share_progress,
                invoice_status=(
                    self.spec.invoice_status
                    or "ACCREC"
                ),
            )

        raise ValueError(
            "Unsupported accounting execution provider."
        )


def build_accounting_execution_bridge(
    *,
    request: Request,
    user_id: str,
    provider: str,
    accounting_intent: str,
) -> AccountingExecutionBridge:
    user_id = str(
        user_id or ""
    ).strip()

    if not user_id:
        raise ValueError(
            "User ID is required."
        )

    client_id = str(
        request.session.get("client_id")
        or ""
    ).strip()

    if not client_id:
        raise ValueError(
            "Client ID not found in session."
        )

    spec = (
        get_accounting_execution_bridge_spec(
            provider,
            accounting_intent,
        )
    )

    # Only now cross into provider runtime imports. At this point
    # an authenticated request is running inside the initialized app.
    runtime = _load_execution_runtime(
        spec.provider
    )

    repo = runtime.repo_factory(
        user_id=user_id,
        integration=spec.provider,
    )

    # Preserve the actual dependency-factory runtime type currently
    # used by the existing bulk routes.
    progress_logger = IntegrationsProgressLog(
        client_id=client_id,
        log_key=spec.progress_log_key,
    )

    if spec.progress_shape == "xero":
        progress = (
            XeroIntegrationsProgressLog
            .get_file_progress_dict()
        )
    else:
        progress = (
            IntegrationsProgressLog
            .get_file_progress_dict()
        )

    context = IntegrationContext(
        request=request,
        user_id=user_id,
        client_id=client_id,
        progress_logger=progress_logger,
        progress=progress,
        doct_type=spec.context_doc_type,
        log_manager=LogManager(),
    )

    client = runtime.client_factory(
        context
    )

    service = runtime.service_factory(
        doc_type=spec.service_doc_type,
        doc_number_prefix=(
            spec.doc_number_prefix
        ),
        context=context,
        repo=repo,
        client=client,
    )

    return AccountingExecutionBridge(
        spec=spec,
        service=service,
        document_factory=(
            runtime.document_factory
        ),
        progress_logger=progress_logger,
        progress=progress,
    )
