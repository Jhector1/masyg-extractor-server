import asyncio
from types import SimpleNamespace

from masyg_extractor.integrations.accounting.shared.operation_progress import (
    ACCOUNTING_OPERATION_PROGRESS_EVENT,
    AccountingOperationProgress,
    emit_accounting_operation_snapshot,
)


def test_accounting_operation_progress_emits_truthful_summary_and_snapshot(monkeypatch):
    emitted = []

    async def fake_emit(event, payload, room=None, to=None):
        emitted.append((event, payload, room, to))

    monkeypatch.setattr(
        "masyg_extractor.integrations.accounting.shared.operation_progress.sio.emit",
        fake_emit,
    )

    context = SimpleNamespace(
        client_id="client-1",
        request=SimpleNamespace(
            headers={
                "X-Accounting-Operation-Id": "op-123",
                "X-Accounting-Action": "send-invoice-in-bulk",
            }
        ),
    )

    async def run_test():
        progress = AccountingOperationProgress.from_context(
            context,
            provider="quickbooks",
            fallback_action="create-invoice",
        )
        await progress.queue_documents(
            [("txn-1", "one.pdf"), ("txn-2", "two.pdf")]
        )
        await progress.mark_all_running(progress=20)
        await progress.succeeded("txn-1", filename="one.pdf")
        await progress.failed("txn-2", filename="two.pdf", error="Rejected")

    asyncio.run(run_test())

    final = emitted[-1][1]
    assert final["summary"] == {
        "total": 2,
        "completed": 2,
        "succeeded": 1,
        "failed": 1,
    }
    assert final["overall_progress"] == 100.0

    emitted.clear()
    restored = asyncio.run(
        emit_accounting_operation_snapshot("client-1", to_sid="sid-after-refresh")
    )
    assert restored is True
    assert len(emitted) == 2
    assert all(to == "sid-after-refresh" for _, _, _, to in emitted)
    assert {payload["document_id"] for _, payload, _, _ in emitted} == {
        "txn-1",
        "txn-2",
    }
