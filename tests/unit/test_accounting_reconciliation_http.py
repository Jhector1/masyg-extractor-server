from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from masyg_extractor.config.jwt_config import (
    get_current_user_from_cookie,
)
from masyg_extractor.integrations.accounting.shared import (
    status_router as router_module,
)
from masyg_extractor.integrations.accounting.shared.reconciliation_service import (
    VerifyAccountingStatusResult,
)
import masyg_extractor.routes as routes_module


ROOT = Path(__file__).resolve().parents[2]

VERIFY_PATH = (
    "/integrations/accounting/verify-status"
)


def make_app(
    *,
    current_user=None,
):
    if current_user is None:
        current_user = {
            "userId": "user-1",
        }

    app = FastAPI()

    # This is the exact router object imported and registered by
    # masyg_extractor.routes.register_routers().
    assert (
        routes_module.accounting_status_router
        is router_module.router
    )

    app.include_router(
        routes_module.accounting_status_router
    )

    async def override_user():
        return current_user

    app.dependency_overrides[
        get_current_user_from_cookie
    ] = override_user

    app.dependency_overrides[router_module.require_active_accounting_subscription] = override_user

    return app


async def post_json(
    app,
    body,
):
    transport = httpx.ASGITransport(
        app=app
    )

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.post(
            VERIFY_PATH,
            json=body,
        )


def request_body(
    **overrides,
):
    value = {
        "provider": "quickbooks",
        "group_id": "group-1",
        "file_id": "file-1",
    }

    value.update(overrides)

    return value


def install_provider_owner(
    monkeypatch,
):
    monkeypatch.setattr(
        router_module,
        "get_accounting_provider",
        lambda provider: object(),
    )


def install_handoff(
    monkeypatch,
    *,
    intent="create_ar_invoice",
):
    calls = []

    async def fake_handoff(
        *,
        group_id,
        file_id,
        current_user,
    ):
        calls.append(
            {
                "group_id": group_id,
                "file_id": file_id,
                "current_user":
                    current_user,
            }
        )

        return {
            "group_id": group_id,
            "file_id": file_id,
            "document_type":
                "sales_invoice",
            "accounting_intent":
                intent,
        }

    monkeypatch.setattr(
        router_module,
        "get_accounting_document_handoff",
        fake_handoff,
    )

    return calls


def install_supported_action(
    monkeypatch,
):
    calls = []

    def fake_action(
        provider,
        intent,
    ):
        calls.append(
            (
                provider,
                intent,
            )
        )

        return object()

    monkeypatch.setattr(
        router_module,
        "get_accounting_execution_action",
        fake_action,
    )

    return calls


def install_runtime(
    monkeypatch,
):
    repo = object()
    provider_client = object()
    calls = []

    def fake_bridge(**kwargs):
        calls.append(kwargs)

        return SimpleNamespace(
            service=SimpleNamespace(
                repo=repo,
                client=provider_client,
            )
        )

    monkeypatch.setattr(
        router_module,
        "build_accounting_execution_bridge",
        fake_bridge,
    )

    return repo, provider_client, calls


def install_verify_result(
    monkeypatch,
    *,
    disposition,
    lookup_outcome,
    reconciled,
    durable_status,
):
    calls = []

    async def fake_verify(**kwargs):
        calls.append(kwargs)

        return VerifyAccountingStatusResult(
            disposition=disposition,
            lookup_outcome=lookup_outcome,
            reconciled=reconciled,
            durable_status=durable_status,
        )

    monkeypatch.setattr(
        router_module,
        "verify_accounting_status",
        fake_verify,
    )

    return calls


def test_registered_router_exposes_exact_post_path():
    app = make_app()

    matches = [
        route
        for route in app.routes
        if getattr(
            route,
            "path",
            None,
        )
        == VERIFY_PATH
    ]

    assert len(matches) == 1

    route = matches[0]

    assert "POST" in route.methods
    assert "GET" not in route.methods


def test_openapi_exposes_verify_status_as_post():
    app = make_app()

    schema = app.openapi()

    assert VERIFY_PATH in schema["paths"]
    assert (
        "post"
        in schema["paths"][VERIFY_PATH]
    )


def test_http_verify_status_happy_path_uses_server_derived_intent(
    monkeypatch,
):
    install_provider_owner(
        monkeypatch
    )

    handoff_calls = install_handoff(
        monkeypatch,
        intent="create_ar_invoice",
    )

    action_calls = (
        install_supported_action(
            monkeypatch
        )
    )

    (
        repo,
        provider_client,
        bridge_calls,
    ) = install_runtime(
        monkeypatch
    )

    verify_calls = (
        install_verify_result(
            monkeypatch,
            disposition="already_created",
            lookup_outcome="found",
            reconciled=True,
            durable_status={
                "status": "succeeded",
                "provider_document_id":
                    "qb-123",
                "provider_document_number":
                    "Inv-123",
            },
        )
    )

    app = make_app()

    response = asyncio.run(
        post_json(
            app,
            request_body(),
        )
    )

    assert response.status_code == 200

    body = response.json()

    assert body == {
        "provider": "quickbooks",
        "group_id": "group-1",
        "file_id": "file-1",
        "disposition":
            "already_created",
        "lookup_outcome": "found",
        "reconciled": True,
        "durable_status": {
            "status": "succeeded",
            "provider_document_id":
                "qb-123",
            "provider_document_number":
                "Inv-123",
        },
    }

    assert handoff_calls == [
        {
            "group_id": "group-1",
            "file_id": "file-1",
            "current_user": {
                "userId": "user-1",
            },
        }
    ]

    assert action_calls == [
        (
            "quickbooks",
            "create_ar_invoice",
        )
    ]

    assert len(bridge_calls) == 1

    assert bridge_calls[0][
        "user_id"
    ] == "user-1"

    assert bridge_calls[0][
        "provider"
    ] == "quickbooks"

    assert bridge_calls[0][
        "accounting_intent"
    ] == "create_ar_invoice"

    assert verify_calls == [
        {
            "provider": "quickbooks",
            "intent":
                "create_ar_invoice",
            "group_id": "group-1",
            "file_id": "file-1",
            "repo": repo,
            "client": provider_client,
        }
    ]


def test_http_unauthenticated_is_401_before_handoff(
    monkeypatch,
):
    async def must_not_handoff(**_kwargs):
        raise AssertionError(
            "Unauthenticated request "
            "reached document handoff."
        )

    monkeypatch.setattr(
        router_module,
        "get_accounting_document_handoff",
        must_not_handoff,
    )

    app = make_app(
        current_user={}
    )

    response = asyncio.run(
        post_json(
            app,
            request_body(),
        )
    )

    assert response.status_code == 401
    assert response.json()["detail"] == (
        "User not authenticated."
    )


@pytest.mark.parametrize(
    "field",
    (
        "accounting_intent",
        "intent",
        "record_type",
        "provider_document_number",
        "provider_document_id",
        "recovery_required",
        "durable_status",
        "action",
        "ready",
    ),
)
def test_http_rejects_client_accounting_authority(
    monkeypatch,
    field,
):
    async def must_not_handoff(**_kwargs):
        raise AssertionError(
            "Spoofed authority field "
            "reached handoff."
        )

    monkeypatch.setattr(
        router_module,
        "get_accounting_document_handoff",
        must_not_handoff,
    )

    app = make_app()

    response = asyncio.run(
        post_json(
            app,
            request_body(
                **{
                    field: "spoofed",
                }
            ),
        )
    )

    assert response.status_code == 400
    assert (
        "accepts only"
        in response.json()["detail"]
    )


def test_http_unsupported_provider_is_400_before_handoff(
    monkeypatch,
):
    def invalid_provider(_provider):
        raise KeyError(
            "unsupported"
        )

    monkeypatch.setattr(
        router_module,
        "get_accounting_provider",
        invalid_provider,
    )

    async def must_not_handoff(**_kwargs):
        raise AssertionError(
            "Unsupported provider "
            "reached handoff."
        )

    monkeypatch.setattr(
        router_module,
        "get_accounting_document_handoff",
        must_not_handoff,
    )

    app = make_app()

    response = asyncio.run(
        post_json(
            app,
            request_body(
                provider="bogus",
            ),
        )
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Unsupported accounting provider."
    )


def test_http_document_not_found_preserves_404(
    monkeypatch,
):
    install_provider_owner(
        monkeypatch
    )

    async def missing_handoff(**_kwargs):
        raise HTTPException(
            status_code=404,
            detail="Document not found.",
        )

    monkeypatch.setattr(
        router_module,
        "get_accounting_document_handoff",
        missing_handoff,
    )

    app = make_app()

    response = asyncio.run(
        post_json(
            app,
            request_body(),
        )
    )

    assert response.status_code == 404
    assert response.json()["detail"] == (
        "Document not found."
    )


def test_http_unsupported_provider_intent_is_not_eligible(
    monkeypatch,
):
    install_provider_owner(
        monkeypatch
    )

    install_handoff(
        monkeypatch,
        intent="create_ap_bill",
    )

    def unsupported(
        provider,
        intent,
    ):
        assert provider == "quickbooks"
        assert intent == "create_ap_bill"

        raise KeyError(
            (provider, intent)
        )

    monkeypatch.setattr(
        router_module,
        "get_accounting_execution_action",
        unsupported,
    )

    def must_not_build(**_kwargs):
        raise AssertionError(
            "Unsupported capability "
            "built runtime."
        )

    monkeypatch.setattr(
        router_module,
        "build_accounting_execution_bridge",
        must_not_build,
    )

    app = make_app()

    response = asyncio.run(
        post_json(
            app,
            request_body(),
        )
    )

    assert response.status_code == 200

    body = response.json()

    assert body["disposition"] == (
        "not_eligible"
    )

    assert body["lookup_outcome"] is None
    assert body["reconciled"] is False


@pytest.mark.parametrize(
    (
        "disposition",
        "lookup_outcome",
        "status",
    ),
    (
        (
            "processing",
            None,
            "sending",
        ),
        (
            "needs_verification",
            "absent",
            "uncertain",
        ),
        (
            "needs_verification",
            "indeterminate",
            "uncertain",
        ),
        (
            "already_created",
            None,
            "succeeded",
        ),
    ),
)
def test_http_preserves_service_disposition(
    monkeypatch,
    disposition,
    lookup_outcome,
    status,
):
    install_provider_owner(
        monkeypatch
    )

    install_handoff(
        monkeypatch
    )

    install_supported_action(
        monkeypatch
    )

    install_runtime(
        monkeypatch
    )

    install_verify_result(
        monkeypatch,
        disposition=disposition,
        lookup_outcome=lookup_outcome,
        reconciled=False,
        durable_status={
            "status": status,
        },
    )

    app = make_app()

    response = asyncio.run(
        post_json(
            app,
            request_body(),
        )
    )

    assert response.status_code == 200

    body = response.json()

    assert (
        body["disposition"]
        == disposition
    )

    assert (
        body["lookup_outcome"]
        == lookup_outcome
    )

    assert (
        body["durable_status"]["status"]
        == status
    )

    assert "ready" not in body
    assert "allow_create" not in body


def test_http_acceptance_never_instantiates_real_provider_client():
    import ast

    source = Path(
        __file__
    ).read_text()

    tree = ast.parse(source)

    forbidden_names = {
        "QuickBooksClientAdapter",
        "XeroClientAdapter",
    }

    forbidden_module_calls = {
        ("httpx", "get"),
        ("httpx", "post"),
        ("requests", "get"),
        ("requests", "post"),
    }

    forbidden_attributes = {
        "send_documents",
    }

    violations = []

    for node in ast.walk(tree):
        if not isinstance(
            node,
            ast.Call,
        ):
            continue

        func = node.func

        if (
            isinstance(
                func,
                ast.Name,
            )
            and func.id in forbidden_names
        ):
            violations.append(
                (
                    node.lineno,
                    func.id,
                )
            )

        if isinstance(
            func,
            ast.Attribute,
        ):
            if (
                func.attr
                in forbidden_attributes
            ):
                violations.append(
                    (
                        node.lineno,
                        func.attr,
                    )
                )

            if (
                isinstance(
                    func.value,
                    ast.Name,
                )
                and (
                    func.value.id,
                    func.attr,
                )
                in forbidden_module_calls
            ):
                violations.append(
                    (
                        node.lineno,
                        (
                            f"{func.value.id}."
                            f"{func.attr}"
                        ),
                    )
                )

    assert violations == []


def test_production_registration_source_uses_same_router_object():
    routes = (
        ROOT
        / "masyg_extractor/routes/__init__.py"
    ).read_text()

    server = (
        ROOT
        / "server.py"
    ).read_text()

    assert (
        "router as accounting_status_router"
        in routes
    )

    assert (
        'app.include_router('
        'accounting_status_router, prefix="")'
        in routes
    )

    assert "register_routers(inner)" in server
