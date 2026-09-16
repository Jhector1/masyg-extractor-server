from pathlib import Path

from masyg_extractor.integrations.accounting.shared.oauth_return import (
    default_accounting_return_to,
    sanitize_accounting_return_to,
)


ROOT = Path(__file__).resolve().parents[2]


def source(relative: str) -> str:
    return (ROOT / relative).read_text()


def test_default_return_preserves_existing_workspace_behavior():
    assert (
        default_accounting_return_to("quickbooks")
        == "/data/shore/quickbooks"
    )
    assert (
        default_accounting_return_to("xero")
        == "/data/shore/xero"
    )


def test_provider_integration_management_routes_are_allowed():
    assert (
        sanitize_accounting_return_to(
            "quickbooks",
            "/integration/quickbooks",
        )
        == "/integration/quickbooks"
    )

    assert (
        sanitize_accounting_return_to(
            "xero",
            "/integration/xero",
        )
        == "/integration/xero"
    )

    assert (
        sanitize_accounting_return_to(
            "quickbooks",
            "/integration",
        )
        == "/integration"
    )


def test_workspace_query_and_fragment_context_are_preserved():
    value = (
        "/data/shore/quickbooks"
        "?intent=create_ar_invoice"
        "&group_id=g1"
        "&file_id=f1"
        "#review"
    )

    assert (
        sanitize_accounting_return_to(
            "quickbooks",
            value,
        )
        == value
    )


def test_external_cross_provider_and_unknown_returns_are_rejected():
    fallback = "/data/shore/quickbooks"

    rejected = (
        "https://evil.example/",
        "http://evil.example/",
        "//evil.example/path",
        r"\evil.example",
        "/integration/xero",
        "/data/shore/xero",
        "/somewhere/else",
        "javascript:alert(1)",
    )

    for value in rejected:
        assert (
            sanitize_accounting_return_to(
                "quickbooks",
                value,
            )
            == fallback
        )


def test_control_characters_are_rejected():
    assert (
        sanitize_accounting_return_to(
            "quickbooks",
            "/integration/quickbooks\nLocation:https://evil.example",
        )
        == "/data/shore/quickbooks"
    )


def test_auth_helper_carries_return_context_in_encrypted_state():
    auth = source(
        "masyg_extractor/integrations/auth_helper.py"
    )

    assert '"user_id": user_id' in auth
    assert '"return_to": safe_return_to' in auth
    assert "json.loads(decrypted_state)" in auth
    assert "user_id = decrypted_state" in auth
    assert (
        'f"{self.CLIENT_URL.rstrip(\'/\')}{return_to}"'
        in auth
    )


def test_auth_helper_keeps_legacy_state_backward_compatibility():
    auth = source(
        "masyg_extractor/integrations/auth_helper.py"
    )

    assert (
        "except (json.JSONDecodeError, TypeError):"
        in auth
    )
    assert "state_payload = None" in auth
    assert "user_id = decrypted_state" in auth


def test_active_provider_login_routes_forward_return_to():
    quickbooks = source(
        "masyg_extractor/integrations/accounting/"
        "quickbooks/authentication/quickbook_auth.py"
    )

    xero = source(
        "masyg_extractor/integrations/accounting/"
        "xero/authentication/xero_auth.py"
    )

    assert "return await qb_auth_helper.login(" in quickbooks
    assert "return await xero_auth_helper.login(" in xero

    for provider in (quickbooks, xero):
        assert "request: Request" in provider
        assert (
            'return_to=request.query_params.get("return_to")'
            in provider
        )
