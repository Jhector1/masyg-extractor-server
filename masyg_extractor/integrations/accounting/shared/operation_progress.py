from __future__ import annotations

import time
from copy import deepcopy
from typing import Any, Iterable
from uuid import uuid4

from masyg_extractor.utils.extensions import sio


ACCOUNTING_OPERATION_PROGRESS_EVENT = "accounting-operation-progress"
_TERMINAL_STATUSES = {"succeeded", "failed"}
_SNAPSHOT_TTL_SECONDS = 6 * 60 * 60
_ACCOUNTING_OPERATION_SNAPSHOTS: dict[str, tuple[float, dict[str, Any]]] = {}


def _clamp_progress(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _summary(documents: dict[str, dict[str, Any]]) -> dict[str, int]:
    statuses = [state["status"] for state in documents.values()]
    succeeded = statuses.count("succeeded")
    failed = statuses.count("failed")
    completed = succeeded + failed
    return {
        "total": len(statuses),
        "completed": completed,
        "succeeded": succeeded,
        "failed": failed,
    }


def _overall_progress(documents: dict[str, dict[str, Any]]) -> float:
    if not documents:
        return 0.0
    return sum(float(state["progress"]) for state in documents.values()) / len(documents)


def _prune_expired_snapshots() -> None:
    cutoff = time.monotonic() - _SNAPSHOT_TTL_SECONDS
    expired = [
        client_id
        for client_id, (updated_at, _) in _ACCOUNTING_OPERATION_SNAPSHOTS.items()
        if updated_at < cutoff
    ]
    for client_id in expired:
        _ACCOUNTING_OPERATION_SNAPSHOTS.pop(client_id, None)


async def emit_accounting_operation_snapshot(
    client_id: str,
    *,
    to_sid: str | None = None,
) -> bool:
    _prune_expired_snapshots()
    record = _ACCOUNTING_OPERATION_SNAPSHOTS.get(client_id)
    if not record:
        return False

    _, snapshot = record
    documents = snapshot["documents"]
    summary = _summary(documents)
    overall_progress = _overall_progress(documents)

    for document_id, state in documents.items():
        payload = {
            "operation_id": snapshot["operation_id"],
            "provider": snapshot["provider"],
            "action": snapshot["action"],
            "document_id": document_id,
            "filename": state["filename"],
            "status": state["status"],
            "progress": state["progress"],
            "message": state["message"],
            "error": state["error"],
            "overall_progress": overall_progress,
            "summary": summary,
        }
        if to_sid:
            await sio.emit(ACCOUNTING_OPERATION_PROGRESS_EVENT, payload, to=to_sid)
        else:
            await sio.emit(ACCOUNTING_OPERATION_PROGRESS_EVENT, payload, room=client_id)

    return bool(documents)


class AccountingOperationProgress:
    # Provider-neutral accounting progress with reconnect snapshots.
    def __init__(
        self,
        *,
        client_id: str,
        provider: str,
        action: str,
        operation_id: str | None = None,
    ) -> None:
        self.client_id = client_id
        self.provider = provider
        self.action = action
        self.operation_id = operation_id or uuid4().hex
        self._documents: dict[str, dict[str, Any]] = {}

    @classmethod
    def from_context(
        cls,
        context: Any,
        *,
        provider: str,
        fallback_action: str,
    ) -> "AccountingOperationProgress":
        headers = context.request.headers
        return cls(
            client_id=context.client_id,
            provider=provider,
            action=headers.get("X-Accounting-Action") or fallback_action,
            operation_id=headers.get("X-Accounting-Operation-Id") or None,
        )

    async def queue_documents(self, documents: Iterable[tuple[str, str]]) -> None:
        for document_id, filename in documents:
            self._documents[document_id] = {
                "filename": filename,
                "status": "queued",
                "progress": 0.0,
                "message": None,
                "error": None,
            }

        self._remember_snapshot()
        for document_id in list(self._documents):
            await self._emit(document_id)

    async def mark_all_running(
        self,
        *,
        progress: float = 10.0,
        message: str = "Preparing document",
    ) -> None:
        for document_id, state in list(self._documents.items()):
            if state["status"] in _TERMINAL_STATUSES:
                continue
            await self.update(
                document_id,
                status="running",
                progress=progress,
                message=message,
            )

    async def update(
        self,
        document_id: str,
        *,
        status: str,
        progress: float,
        filename: str | None = None,
        message: str | None = None,
        error: str | None = None,
    ) -> None:
        state = self._documents.setdefault(
            document_id,
            {
                "filename": filename or document_id,
                "status": "queued",
                "progress": 0.0,
                "message": None,
                "error": None,
            },
        )

        if state["status"] in _TERMINAL_STATUSES:
            return

        state["status"] = status
        state["progress"] = 100.0 if status in _TERMINAL_STATUSES else _clamp_progress(progress)
        if filename:
            state["filename"] = filename
        state["message"] = message
        state["error"] = error

        self._remember_snapshot()
        await self._emit(document_id)

    async def succeeded(
        self,
        document_id: str,
        *,
        filename: str | None = None,
        message: str = "Created successfully",
    ) -> None:
        await self.update(
            document_id,
            status="succeeded",
            progress=100.0,
            filename=filename,
            message=message,
        )

    async def failed(
        self,
        document_id: str,
        *,
        filename: str | None = None,
        error: str,
    ) -> None:
        await self.update(
            document_id,
            status="failed",
            progress=100.0,
            filename=filename,
            error=error,
        )

    async def fail_remaining(self, error: str) -> None:
        for document_id, state in list(self._documents.items()):
            if state["status"] in _TERMINAL_STATUSES:
                continue
            await self.failed(
                document_id,
                filename=state["filename"],
                error=error,
            )

    def summary(self) -> dict[str, int]:
        return _summary(self._documents)

    def overall_progress(self) -> float:
        return _overall_progress(self._documents)

    def result_payload(self) -> dict[str, Any]:
        """Safe provider-neutral completion envelope for HTTP reconciliation."""
        return {
            "operation_id": self.operation_id,
            "provider": self.provider,
            "action": self.action,
            "results": [
                {
                    "document_id": document_id,
                    "filename": state["filename"],
                    "status": state["status"],
                    "progress": state["progress"],
                    "message": state["message"],
                    "error": state["error"],
                }
                for document_id, state in self._documents.items()
            ],
            "overall_progress": self.overall_progress(),
            "summary": self.summary(),
        }

    def _remember_snapshot(self) -> None:
        _prune_expired_snapshots()
        _ACCOUNTING_OPERATION_SNAPSHOTS[self.client_id] = (
            time.monotonic(),
            {
                "operation_id": self.operation_id,
                "provider": self.provider,
                "action": self.action,
                "documents": deepcopy(self._documents),
            },
        )

    async def _emit(self, document_id: str) -> None:
        self._remember_snapshot()
        state = self._documents[document_id]
        payload = {
            "operation_id": self.operation_id,
            "provider": self.provider,
            "action": self.action,
            "document_id": document_id,
            "filename": state["filename"],
            "status": state["status"],
            "progress": state["progress"],
            "message": state["message"],
            "error": state["error"],
            "overall_progress": self.overall_progress(),
            "summary": self.summary(),
        }
        await sio.emit(ACCOUNTING_OPERATION_PROGRESS_EVENT, payload, room=self.client_id)
