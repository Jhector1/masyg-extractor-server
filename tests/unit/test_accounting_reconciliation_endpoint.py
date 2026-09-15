from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from masyg_extractor.integrations.accounting.shared import (
    status_router as router_module,
)
from masyg_extractor.integrations.accounting.shared.reconciliation_service import (
    VerifyAccountingStatusResult,
)


ROOT = Path(__file__).resolve().parents[2]


def request():
    return SimpleNamespace(
        session={
            "client_id": "client-1",
        }
    )


def handoff(
    *,
    intent="create_ar_invoice",
):
    return {
        "group_id": "group-1",
        "file_id": "file-1",
        "document_type": "sales_invoice",
        "accounting_intent": intent,
    }


def payload(
    **extra,
):
    value = {
        "provider": "quickbooks",
        "group_id": "group-1",
        "file_id": "file-1",
    }

    value.update(extra)

    return value


def run(
    body,
    *,
    user=None,
):
    if user is None:
        user = {
            "userId": "user-1",
        }

    return asyncio.run(
        router_module
        .post_accounting_verify_status(
            request(),
            body,
            user,
        )
    )


def install_provider_validation(
    monkeypatch,
):
    monkeypatch.setattr(
        router_module,
        "get_accounting_provider",
        lambda provider: object(),
    )


def install_supported_action(
    monkeypatch,
):
    monkeypatch.setattr(
        router_module,
        "get_accounting_execution_action",
        lambda provider, intent: object(),
    )


def test_verify_requires_authenticated_user_before_handoff(
    monkeypatch,
):
    async def must_not_handoff(**_kwargs):
        raise AssertionError(
            "Unauthenticated request reached handoff."
        )

    monkeypatch.setattr(
        router_module,
        "get_accounting_document_handoff",
        must_not_handoff,
    )

    with pytest.raises(
        HTTPException,
    ) as error:
        run(
            payload(),
            user={},
        )

    assert error.value.status_code == 401


@pytest.mark.parametrize(
    "authority_field",
    (
        "intent",
        "accounting_intent",
        "record_type",
        "provider_document_number",
        "provider_document_id",
        "durable_status",
        "recovery_required",
        "action",
        "state",
        "ready",
    ),
)
def test_verify_rejects_client_accounting_authority_fields(
    monkeypatch,
    authority_field,
):
    async def must_not_handoff(**_kwargs):
        raise AssertionError(
            "Rejected authority field reached handoff."
        )

    monkeypatch.setattr(
        router_module,
        "get_accounting_document_handoff",
        must_not_handoff,
    )

    body = payload(
        **{
            authority_field: "spoofed",
        }
    )

    with pytest.raises(
        HTTPException,
    ) as error:
        run(body)

    assert error.value.status_code == 400
    assert (
        "accepts only"
        in error.value.detail
    )


@pytest.mark.parametrize(
    "field",
    (
        "provider",
        "group_id",
        "file_id",
    ),
)
def test_verify_requires_minimum_identity(
    monkeypatch,
    field,
):
    body = payload()
    body[field] = ""

    with pytest.raises(
        HTTPException,
    ) as error:
        run(body)

    assert error.value.status_code == 400


def test_verify_validates_provider_before_document_handoff(
    monkeypatch,
):
    def invalid_provider(_provider):
        raise KeyError(
            "invalid provider"
        )

    monkeypatch.setattr(
        router_module,
        "get_accounting_provider",
        invalid_provider,
    )

    async def must_not_handoff(**_kwargs):
        raise AssertionError(
            "Invalid provider reached document owner."
        )

    monkeypatch.setattr(
        router_module,
        "get_accounting_document_handoff",
        must_not_handoff,
    )

    with pytest.raises(
        HTTPException,
    ) as error:
        run(payload())

    assert error.value.status_code == 400
    assert (
        error.value.detail
        == "Unsupported accounting provider."
    )


def test_verify_reuses_authenticated_document_handoff_owner(
    monkeypatch,
):
    install_provider_validation(
        monkeypatch
    )

    handoff_calls = []

    async def fake_handoff(
        *,
        group_id,
        file_id,
        current_user,
    ):
        handoff_calls.append(
            {
                "group_id": group_id,
                "file_id": file_id,
                "current_user": current_user,
            }
        )

        return handoff()

    monkeypatch.setattr(
        router_module,
        "get_accounting_document_handoff",
        fake_handoff,
    )

    def unsupported(
        provider,
        intent,
    ):
        assert provider == "quickbooks"
        assert intent == "create_ar_invoice"
        raise KeyError(
            "stop before runtime"
        )

    monkeypatch.setattr(
        router_module,
        "get_accounting_execution_action",
        unsupported,
    )

    result = run(
        payload()
    )

    assert handoff_calls == [
        {
            "group_id": "group-1",
            "file_id": "file-1",
            "current_user": {
                "userId": "user-1",
            },
        }
    ]

    assert (
        result["disposition"]
        == "not_eligible"
    )


def test_unsupported_provider_intent_never_builds_runtime(
    monkeypatch,
):
    install_provider_validation(
        monkeypatch
    )

    async def fake_handoff(**_kwargs):
        return handoff(
            intent="create_ap_bill",
        )

    monkeypatch.setattr(
        router_module,
        "get_accounting_document_handoff",
        fake_handoff,
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
            "Unsupported capability built provider runtime."
        )

    monkeypatch.setattr(
        router_module,
        "build_accounting_execution_bridge",
        must_not_build,
    )

    async def must_not_verify(**_kwargs):
        raise AssertionError(
            "Unsupported capability reached verification."
        )

    monkeypatch.setattr(
        router_module,
        "verify_accounting_status",
        must_not_verify,
    )

    result = run(
        payload()
    )

    assert result == {
        "provider": "quickbooks",
        "group_id": "group-1",
        "file_id": "file-1",
        "disposition": "not_eligible",
        "lookup_outcome": None,
        "reconciled": False,
        "durable_status": None,
    }


def test_supported_route_uses_server_derived_intent_and_runtime(
    monkeypatch,
):
    install_provider_validation(
        monkeypatch
    )

    async def fake_handoff(**_kwargs):
        return handoff(
            intent="create_ar_invoice",
        )

    monkeypatch.setattr(
        router_module,
        "get_accounting_document_handoff",
        fake_handoff,
    )

    action_calls = []

    def fake_action(
        provider,
        intent,
    ):
        action_calls.append(
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

    repo = object()
    provider_client = object()

    bridge_calls = []

    def fake_bridge(**kwargs):
        bridge_calls.append(kwargs)

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

    verify_calls = []

    async def fake_verify(**kwargs):
        verify_calls.append(kwargs)

        return VerifyAccountingStatusResult(
            disposition="needs_verification",
            lookup_outcome="indeterminate",
            reconciled=False,
            durable_status={
                "status": "uncertain",
                "provider":
                    "quickbooks",
                "intent":
                    "create_ar_invoice",
                "group_id":
                    "group-1",
                "file_id":
                    "file-1",
            },
        )

    monkeypatch.setattr(
        router_module,
        "verify_accounting_status",
        fake_verify,
    )

    req = request()

    result = asyncio.run(
        router_module
        .post_accounting_verify_status(
            req,
            payload(),
            {
                "userId": "user-1",
            },
        )
    )

    assert action_calls == [
        (
            "quickbooks",
            "create_ar_invoice",
        )
    ]

    assert len(bridge_calls) == 1

    assert bridge_calls[0] == {
        "request": req,
        "user_id": "user-1",
        "provider": "quickbooks",
        "accounting_intent":
            "create_ar_invoice",
    }

    assert verify_calls == [
        {
            "provider": "quickbooks",
            "intent": "create_ar_invoice",
            "group_id": "group-1",
            "file_id": "file-1",
            "repo": repo,
            "client": provider_client,
        }
    ]

    assert result == {
        "provider": "quickbooks",
        "group_id": "group-1",
        "file_id": "file-1",
        "disposition":
            "needs_verification",
        "lookup_outcome":
            "indeterminate",
        "reconciled": False,
        "durable_status": {
            "status": "uncertain",
            "provider": "quickbooks",
            "intent":
                "create_ar_invoice",
            "group_id": "group-1",
            "file_id": "file-1",
        },
    }


def test_route_does_not_reflect_or_accept_client_intent(
    monkeypatch,
):
    install_provider_validation(
        monkeypatch
    )

    async def must_not_handoff(**_kwargs):
        raise AssertionError(
            "Spoofed intent must fail before handoff."
        )

    monkeypatch.setattr(
        router_module,
        "get_accounting_document_handoff",
        must_not_handoff,
    )

    with pytest.raises(
        HTTPException,
    ) as error:
        run(
            payload(
                accounting_intent=
                    "create_ap_bill",
            )
        )

    assert error.value.status_code == 400


def test_canonical_identity_mismatch_fails_closed(
    monkeypatch,
):
    install_provider_validation(
        monkeypatch
    )

    async def fake_handoff(**_kwargs):
        value = handoff()
        value["file_id"] = "different-file"
        return value

    monkeypatch.setattr(
        router_module,
        "get_accounting_document_handoff",
        fake_handoff,
    )

    with pytest.raises(
        HTTPException,
    ) as error:
        run(payload())

    assert error.value.status_code == 409


def test_missing_runtime_repo_or_client_fails_closed(
    monkeypatch,
):
    install_provider_validation(
        monkeypatch
    )

    async def fake_handoff(**_kwargs):
        return handoff()

    monkeypatch.setattr(
        router_module,
        "get_accounting_document_handoff",
        fake_handoff,
    )

    install_supported_action(
        monkeypatch
    )

    monkeypatch.setattr(
        router_module,
        "build_accounting_execution_bridge",
        lambda **_kwargs: SimpleNamespace(
            service=SimpleNamespace(
                repo=None,
                client=None,
            )
        ),
    )

    with pytest.raises(
        HTTPException,
    ) as error:
        run(payload())

    assert error.value.status_code == 500


def test_bridge_construction_error_is_public_400(
    monkeypatch,
):
    install_provider_validation(
        monkeypatch
    )

    async def fake_handoff(**_kwargs):
        return handoff()

    monkeypatch.setattr(
        router_module,
        "get_accounting_document_handoff",
        fake_handoff,
    )

    install_supported_action(
        monkeypatch
    )

    def failing_bridge(**_kwargs):
        raise ValueError(
            "Client ID not found in session."
        )

    monkeypatch.setattr(
        router_module,
        "build_accounting_execution_bridge",
        failing_bridge,
    )

    with pytest.raises(
        HTTPException,
    ) as error:
        run(payload())

    assert error.value.status_code == 400
    assert (
        error.value.detail
        == "Client ID not found in session."
    )


def test_route_returns_service_disposition_without_inventing_retry_state(
    monkeypatch,
):
    install_provider_validation(
        monkeypatch
    )

    async def fake_handoff(**_kwargs):
        return handoff()

    monkeypatch.setattr(
        router_module,
        "get_accounting_document_handoff",
        fake_handoff,
    )

    install_supported_action(
        monkeypatch
    )

    monkeypatch.setattr(
        router_module,
        "build_accounting_execution_bridge",
        lambda **_kwargs: SimpleNamespace(
            service=SimpleNamespace(
                repo=object(),
                client=object(),
            )
        ),
    )

    async def fake_verify(**_kwargs):
        return VerifyAccountingStatusResult(
            disposition="processing",
            lookup_outcome=None,
            reconciled=False,
            durable_status={
                "status": "sending",
                "recovery_required":
                    False,
            },
        )

    monkeypatch.setattr(
        router_module,
        "verify_accounting_status",
        fake_verify,
    )

    result = run(payload())

    assert result["disposition"] == "processing"
    assert result["lookup_outcome"] is None
    assert result["reconciled"] is False

    assert "ready" not in result
    assert "allow_create" not in result


def test_verify_route_source_reuses_existing_owners_without_mutation():
    source = (
        ROOT
        / "masyg_extractor/integrations/accounting/"
          "shared/status_router.py"
    ).read_text()

    start = source.index(
        '@router.post("/verify-status")'
    )

    end = source.index(
        '@router.get("/status")',
        start,
    )

    block = source[start:end]

    required = (
        "get_accounting_provider",
        "get_accounting_document_handoff",
        "get_accounting_execution_action",
        "build_accounting_execution_bridge",
        "verify_accounting_status",
    )

    for token in required:
        assert token in block, token

    forbidden = (
        "get_firestore_client(",
        "document_get(",
        "read_accounting_durable_status(",
        "release_record_claim(",
        "claim_record(",
        "mark_record_uncertain(",
        "finalize_record(",
        "finalize_reconciled_record(",
        ".send_documents(",
        ".request(",
    )

    for token in forbidden:
        assert token not in block, token


def test_verify_route_owner_order_is_fail_closed():
    source = (
        ROOT
        / "masyg_extractor/integrations/accounting/"
          "shared/status_router.py"
    ).read_text()

    start = source.index(
        '@router.post("/verify-status")'
    )

    end = source.index(
        '@router.get("/status")',
        start,
    )

    block = source[start:end]

    auth = block.index(
        'current_user.get("userId")'
    )

    allowlist = block.index(
        "allowed_fields ="
    )

    provider_validation = block.index(
        "get_accounting_provider("
    )

    handoff_owner = block.index(
        "get_accounting_document_handoff("
    )

    capability = block.index(
        "get_accounting_execution_action("
    )

    bridge = block.index(
        "build_accounting_execution_bridge("
    )

    verify = block.index(
        "verify_accounting_status("
    )

    assert (
        auth
        < allowlist
        < provider_validation
        < handoff_owner
        < capability
        < bridge
        < verify
    )
