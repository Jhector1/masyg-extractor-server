from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from masyg_extractor.integrations.accounting.shared import (
    status_router as router_module,
)


class FakeSnapshot:
    def __init__(
        self,
        *,
        exists: bool,
        data=None,
    ):
        self.exists = exists
        self._data = data

    def to_dict(self):
        return self._data


class FakeRef:
    def __init__(
        self,
        parts=(),
    ):
        self.parts = tuple(parts)

    def collection(
        self,
        name,
    ):
        return FakeRef(
            self.parts
            + (
                "collection",
                name,
            )
        )

    def document(
        self,
        name,
    ):
        return FakeRef(
            self.parts
            + (
                "document",
                name,
            )
        )


class FakeClient:
    def collection(
        self,
        name,
    ):
        return FakeRef(
            (
                "collection",
                name,
            )
        )


def request():
    return SimpleNamespace(
        session={
            "client_id": "client-1",
        }
    )


def ready_document(
    *,
    file_id="file-1",
    group_id="group-1",
    intent="create_ar_invoice",
):
    return {
        "provider": "quickbooks",
        "group_id": group_id,
        "file_id": file_id,
        "document_type": "sales_invoice",
        "accounting_intent": intent,
        "state": "ready",
        "reason": None,
        "durable_status": {
            "status": "none",
        },
    }


def test_execute_requires_exact_boolean_confirmation(
    monkeypatch,
):
    called = False

    async def fake_preflight(*_args):
        nonlocal called
        called = True
        raise AssertionError(
            "Preflight must not run without confirmation."
        )

    monkeypatch.setattr(
        router_module,
        "post_accounting_batch_preflight",
        fake_preflight,
    )

    for confirmation in (
        None,
        False,
        1,
        "true",
        {},
    ):
        payload = {
            "provider": "quickbooks",
            "documents": [
                {
                    "group_id": "group-1",
                    "file_id": "file-1",
                }
            ],
        }

        if confirmation is not None:
            payload["confirmed"] = (
                confirmation
            )

        with pytest.raises(
            HTTPException,
        ) as error:
            asyncio.run(
                router_module
                .post_accounting_execution(
                    request(),
                    payload,
                    {
                        "userId": "user-1",
                    },
                )
            )

        assert error.value.status_code == 400

    assert called is False


def test_execute_rejects_missing_authenticated_user():
    with pytest.raises(
        HTTPException,
    ) as error:
        asyncio.run(
            router_module
            .post_accounting_execution(
                request(),
                {
                    "confirmed": True,
                    "provider": "quickbooks",
                    "documents": [],
                },
                {},
            )
        )

    assert error.value.status_code == 401


def test_execute_requires_session_client_id():
    with pytest.raises(
        HTTPException,
    ) as error:
        asyncio.run(
            router_module
            .post_accounting_execution(
                SimpleNamespace(
                    session={}
                ),
                {
                    "confirmed": True,
                    "provider": "quickbooks",
                    "documents": [],
                },
                {
                    "userId": "user-1",
                },
            )
        )

    assert error.value.status_code == 400


def test_execute_uses_fresh_server_plan_and_preserves_blocked_documents(
    monkeypatch,
):
    preflight_calls = []
    materializer_calls = []
    send_calls = []

    ready = ready_document()

    blocked = {
        "provider": "quickbooks",
        "group_id": "group-2",
        "file_id": "blocked-1",
        "document_type": "vendor_bill",
        "accounting_intent": "create_ap_bill",
        "state": "unsupported",
        "reason": "Unsupported.",
        "durable_status": None,
    }

    fresh_preflight = {
        "provider": "quickbooks",
        "documents": [
            ready,
            blocked,
        ],
    }

    plan = {
        "provider": "quickbooks",
        "selected": 2,
        "executable": 1,
        "blocked": 1,
        "groups": [
            {
                "provider": "quickbooks",
                "accounting_intent":
                    "create_ar_invoice",
                "count": 1,
                "documents": [
                    ready,
                ],
            }
        ],
        "blocked_documents": [
            blocked,
        ],
    }

    async def fake_preflight(
        payload,
        current_user,
    ):
        preflight_calls.append(
            (
                payload,
                current_user,
            )
        )
        return fresh_preflight

    monkeypatch.setattr(
        router_module,
        "post_accounting_batch_preflight",
        fake_preflight,
    )

    monkeypatch.setattr(
        router_module,
        "build_accounting_execution_plan",
        lambda **_kwargs: plan,
    )

    async def fake_get_firestore_client():
        return FakeClient()

    monkeypatch.setattr(
        router_module,
        "get_firestore_client",
        fake_get_firestore_client,
    )

    async def fake_document_get(ref):
        assert ref.parts == (
            "collection",
            "users",
            "document",
            "user-1",
            "collection",
            "groups",
            "document",
            "group-1",
            "collection",
            "files",
            "document",
            "file-1",
        )

        return FakeSnapshot(
            exists=True,
            data={
                "documentType":
                    "sales_invoice",
                "customer_name":
                    "Customer",
                "line_items": [],
            },
        )

    monkeypatch.setattr(
        router_module,
        "document_get",
        fake_document_get,
    )

    monkeypatch.setattr(
        router_module,
        "resolve_accounting_document_handoff",
        lambda document, *, group_id, file_id: {
            "group_id": group_id,
            "file_id": file_id,
            "document_type":
                "sales_invoice",
            "accounting_intent":
                "create_ar_invoice",
        },
    )

    class FakeBridge:
        document_factory = object()

        async def send_documents(
            self,
            documents,
        ):
            send_calls.append(
                documents
            )
            return {
                "completed": 1,
            }

    bridge_calls = []

    def fake_build_bridge(
        **kwargs,
    ):
        bridge_calls.append(
            kwargs
        )
        return FakeBridge()

    monkeypatch.setattr(
        router_module,
        "build_accounting_execution_bridge",
        fake_build_bridge,
    )

    materialized = object()

    def fake_materialize(
        document,
        *,
        group_id,
        file_id,
        factory,
    ):
        materializer_calls.append(
            (
                document,
                group_id,
                file_id,
                factory,
            )
        )
        return materialized

    monkeypatch.setattr(
        router_module,
        "materialize_accounting_document",
        fake_materialize,
    )

    payload = {
        "confirmed": True,
        "provider": "quickbooks",
        "documents": [
            {
                "group_id": "group-1",
                "file_id": "file-1",
            },
            {
                "group_id": "group-2",
                "file_id": "blocked-1",
            },
        ],
        # Deliberately malicious/stale fields. They must be ignored.
        "groups": [
            {
                "accounting_intent":
                    "create_ap_bill",
            }
        ],
        "ready": True,
        "action": "send-anything",
    }

    result = asyncio.run(
        router_module
        .post_accounting_execution(
            request(),
            payload,
            {
                "userId": "user-1",
            },
        )
    )

    assert preflight_calls == [
        (
            {
                "provider": "quickbooks",
                "documents": payload[
                    "documents"
                ],
            },
            {
                "userId": "user-1",
            },
        )
    ]

    assert result["plan"] is plan

    assert (
        result["plan"][
            "blocked_documents"
        ]
        == [
            blocked,
        ]
    )

    assert result[
        "runtime_blocked"
    ] == []

    assert bridge_calls == [
        {
            "request": request()
            if False
            else bridge_calls[0]["request"],
            "user_id": "user-1",
            "provider": "quickbooks",
            "accounting_intent":
                "create_ar_invoice",
        }
    ]

    assert (
        bridge_calls[0]["user_id"]
        == "user-1"
    )
    assert (
        bridge_calls[0]["provider"]
        == "quickbooks"
    )
    assert (
        bridge_calls[0][
            "accounting_intent"
        ]
        == "create_ar_invoice"
    )

    assert len(materializer_calls) == 1

    _, group_id, file_id, factory = (
        materializer_calls[0]
    )

    assert group_id == "group-1"
    assert file_id == "file-1"
    assert factory is FakeBridge.document_factory

    assert send_calls == [
        [
            materialized,
        ]
    ]

    assert result["executions"] == [
        {
            "provider": "quickbooks",
            "accounting_intent":
                "create_ar_invoice",
            "status": "completed",
            "documents": [
                {
                    "group_id": "group-1",
                    "file_id": "file-1",
                }
            ],
            "result": {
                "completed": 1,
            },
        }
    ]


def test_execute_rechecks_handoff_and_does_not_send_changed_intent(
    monkeypatch,
):
    ready = ready_document()

    async def fake_preflight(
        _payload,
        _current_user,
    ):
        return {
            "provider": "quickbooks",
            "documents": [
                ready,
            ],
        }

    monkeypatch.setattr(
        router_module,
        "post_accounting_batch_preflight",
        fake_preflight,
    )

    monkeypatch.setattr(
        router_module,
        "build_accounting_execution_plan",
        lambda **_kwargs: {
            "provider": "quickbooks",
            "selected": 1,
            "executable": 1,
            "blocked": 0,
            "groups": [
                {
                    "provider":
                        "quickbooks",
                    "accounting_intent":
                        "create_ar_invoice",
                    "count": 1,
                    "documents": [
                        ready,
                    ],
                }
            ],
            "blocked_documents": [],
        },
    )

    async def fake_get_firestore_client():
        return FakeClient()

    monkeypatch.setattr(
        router_module,
        "get_firestore_client",
        fake_get_firestore_client,
    )

    monkeypatch.setattr(
        router_module,
        "document_get",
        lambda _ref: None,
    )

    async def fake_document_get(_ref):
        return FakeSnapshot(
            exists=True,
            data={
                "documentType":
                    "vendor_bill",
            },
        )

    monkeypatch.setattr(
        router_module,
        "document_get",
        fake_document_get,
    )

    monkeypatch.setattr(
        router_module,
        "resolve_accounting_document_handoff",
        lambda document, *, group_id, file_id: {
            "group_id": group_id,
            "file_id": file_id,
            "document_type":
                "vendor_bill",
            "accounting_intent":
                "create_ap_bill",
        },
    )

    def must_not_build_bridge(**_kwargs):
        raise AssertionError(
            "Changed intent must not reach provider bridge."
        )

    monkeypatch.setattr(
        router_module,
        "build_accounting_execution_bridge",
        must_not_build_bridge,
    )

    result = asyncio.run(
        router_module
        .post_accounting_execution(
            request(),
            {
                "confirmed": True,
                "provider": "quickbooks",
                "documents": [
                    {
                        "group_id":
                            "group-1",
                        "file_id":
                            "file-1",
                    }
                ],
            },
            {
                "userId": "user-1",
            },
        )
    )

    assert result["executions"] == []

    assert len(
        result["runtime_blocked"]
    ) == 1

    assert (
        result["runtime_blocked"][0][
            "file_id"
        ]
        == "file-1"
    )

    assert (
        "changed after preflight"
        in result[
            "runtime_blocked"
        ][0]["reason"]
    )


def test_execute_partially_materializes_group_and_sends_only_valid_documents(
    monkeypatch,
):
    first = ready_document(
        file_id="file-1"
    )

    second = ready_document(
        file_id="file-2"
    )

    async def fake_preflight(
        _payload,
        _current_user,
    ):
        return {
            "provider": "quickbooks",
            "documents": [
                first,
                second,
            ],
        }

    monkeypatch.setattr(
        router_module,
        "post_accounting_batch_preflight",
        fake_preflight,
    )

    monkeypatch.setattr(
        router_module,
        "build_accounting_execution_plan",
        lambda **_kwargs: {
            "provider": "quickbooks",
            "selected": 2,
            "executable": 2,
            "blocked": 0,
            "groups": [
                {
                    "provider":
                        "quickbooks",
                    "accounting_intent":
                        "create_ar_invoice",
                    "count": 2,
                    "documents": [
                        first,
                        second,
                    ],
                }
            ],
            "blocked_documents": [],
        },
    )

    async def fake_get_firestore_client():
        return FakeClient()

    monkeypatch.setattr(
        router_module,
        "get_firestore_client",
        fake_get_firestore_client,
    )

    async def fake_document_get(ref):
        file_id = ref.parts[-1]

        return FakeSnapshot(
            exists=True,
            data={
                "file_id_marker":
                    file_id,
            },
        )

    monkeypatch.setattr(
        router_module,
        "document_get",
        fake_document_get,
    )

    monkeypatch.setattr(
        router_module,
        "resolve_accounting_document_handoff",
        lambda document, *, group_id, file_id: {
            "group_id": group_id,
            "file_id": file_id,
            "document_type":
                "sales_invoice",
            "accounting_intent":
                "create_ar_invoice",
        },
    )

    sent = []

    class FakeBridge:
        document_factory = object()

        async def send_documents(
            self,
            documents,
        ):
            sent.extend(
                documents
            )
            return {
                "ok": True,
            }

    monkeypatch.setattr(
        router_module,
        "build_accounting_execution_bridge",
        lambda **_kwargs: FakeBridge(),
    )

    valid_document = object()

    def fake_materialize(
        source,
        *,
        group_id,
        file_id,
        factory,
    ):
        if file_id == "file-2":
            raise ValueError(
                "Invalid accounting source."
            )

        assert group_id == "group-1"
        return valid_document

    monkeypatch.setattr(
        router_module,
        "materialize_accounting_document",
        fake_materialize,
    )

    result = asyncio.run(
        router_module
        .post_accounting_execution(
            request(),
            {
                "confirmed": True,
                "provider": "quickbooks",
                "documents": [
                    {
                        "group_id":
                            "group-1",
                        "file_id":
                            "file-1",
                    },
                    {
                        "group_id":
                            "group-1",
                        "file_id":
                            "file-2",
                    },
                ],
            },
            {
                "userId": "user-1",
            },
        )
    )

    assert sent == [
        valid_document,
    ]

    assert len(
        result["runtime_blocked"]
    ) == 1

    assert (
        result["runtime_blocked"][0][
            "file_id"
        ]
        == "file-2"
    )

    assert (
        result["executions"][0][
            "documents"
        ]
        == [
            {
                "group_id":
                    "group-1",
                "file_id":
                    "file-1",
            }
        ]
    )


def test_execute_continues_after_one_group_execution_failure(
    monkeypatch,
):
    ar = ready_document(
        file_id="ar-1",
        intent="create_ar_invoice",
    )

    receipt = ready_document(
        file_id="sr-1",
        intent="create_sales_receipt",
    )

    async def fake_preflight(
        _payload,
        _current_user,
    ):
        return {
            "provider": "quickbooks",
            "documents": [
                ar,
                receipt,
            ],
        }

    monkeypatch.setattr(
        router_module,
        "post_accounting_batch_preflight",
        fake_preflight,
    )

    monkeypatch.setattr(
        router_module,
        "build_accounting_execution_plan",
        lambda **_kwargs: {
            "provider": "quickbooks",
            "selected": 2,
            "executable": 2,
            "blocked": 0,
            "groups": [
                {
                    "provider":
                        "quickbooks",
                    "accounting_intent":
                        "create_ar_invoice",
                    "count": 1,
                    "documents": [
                        ar,
                    ],
                },
                {
                    "provider":
                        "quickbooks",
                    "accounting_intent":
                        "create_sales_receipt",
                    "count": 1,
                    "documents": [
                        receipt,
                    ],
                },
            ],
            "blocked_documents": [],
        },
    )

    async def fake_get_firestore_client():
        return FakeClient()

    monkeypatch.setattr(
        router_module,
        "get_firestore_client",
        fake_get_firestore_client,
    )

    async def fake_document_get(_ref):
        return FakeSnapshot(
            exists=True,
            data={
                "documentType":
                    "sales_invoice",
            },
        )

    monkeypatch.setattr(
        router_module,
        "document_get",
        fake_document_get,
    )

    def fake_handoff(
        document,
        *,
        group_id,
        file_id,
    ):
        intent = (
            "create_sales_receipt"
            if file_id == "sr-1"
            else "create_ar_invoice"
        )

        return {
            "group_id": group_id,
            "file_id": file_id,
            "document_type":
                "sales_receipt",
            "accounting_intent":
                intent,
        }

    monkeypatch.setattr(
        router_module,
        "resolve_accounting_document_handoff",
        fake_handoff,
    )

    monkeypatch.setattr(
        router_module,
        "materialize_accounting_document",
        lambda source, **kwargs: (
            kwargs["file_id"]
        ),
    )

    class FailingBridge:
        document_factory = object()

        async def send_documents(
            self,
            documents,
        ):
            raise RuntimeError(
                "first group failed"
            )

    class SuccessfulBridge:
        document_factory = object()

        async def send_documents(
            self,
            documents,
        ):
            return {
                "sent": documents,
            }

    def fake_build_bridge(
        **kwargs,
    ):
        if (
            kwargs["accounting_intent"]
            == "create_ar_invoice"
        ):
            return FailingBridge()

        return SuccessfulBridge()

    monkeypatch.setattr(
        router_module,
        "build_accounting_execution_bridge",
        fake_build_bridge,
    )

    result = asyncio.run(
        router_module
        .post_accounting_execution(
            request(),
            {
                "confirmed": True,
                "provider": "quickbooks",
                "documents": [
                    {
                        "group_id":
                            "group-1",
                        "file_id":
                            "ar-1",
                    },
                    {
                        "group_id":
                            "group-1",
                        "file_id":
                            "sr-1",
                    },
                ],
            },
            {
                "userId": "user-1",
            },
        )
    )

    assert len(
        result["executions"]
    ) == 2

    assert (
        result["executions"][0][
            "status"
        ]
        == "failed"
    )

    assert (
        result["executions"][1][
            "status"
        ]
        == "completed"
    )


def test_execute_router_does_not_own_provider_write_logic():
    source = (
        router_module.__file__
    )

    text = open(
        source,
        encoding="utf-8",
    ).read()

    assert (
        '@router.post("/execute")'
        in text
    )

    assert (
        "post_accounting_batch_preflight("
        in text
    )

    assert (
        "build_accounting_execution_plan("
        in text
    )

    assert (
        "resolve_accounting_document_handoff("
        in text
    )

    assert (
        "materialize_accounting_document("
        in text
    )

    assert (
        "build_accounting_execution_bridge("
        in text
    )

    forbidden = (
        "QuickBooksClientAdapter",
        "XeroClientAdapter",
        "QuickBooksDocumentService",
        "XeroDocumentService",
        "normalize_payload(",
        "claim_record(",
        "finalize_record(",
        "mark_record_uncertain(",
        "release_record_claim(",
    )

    for token in forbidden:
        assert token not in text
