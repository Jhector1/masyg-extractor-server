import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

CANONICAL_PROVIDER_ROOTS = (
    ROOT / "masyg_extractor/integrations/accounting/quickbooks",
    ROOT / "masyg_extractor/integrations/accounting/xero",
)

FORBIDDEN_LEGACY_PREFIXES = (
    "masyg_extractor.integrations.quickbooks",
    "masyg_extractor.integrations.xero",
)


def _imports(path: Path):
    tree = ast.parse(path.read_text(), filename=str(path))
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        elif isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
    return modules


def test_canonical_provider_trees_have_no_legacy_provider_imports():
    offenders = []

    for provider_root in CANONICAL_PROVIDER_ROOTS:
        for path in provider_root.rglob("*.py"):
            for module in _imports(path):
                if module.startswith(FORBIDDEN_LEGACY_PREFIXES):
                    offenders.append(
                        f"{path.relative_to(ROOT)} imports {module}"
                    )

    assert offenders == []


def test_auth_helper_uses_shared_oauth_state_owner():
    path = ROOT / "masyg_extractor/integrations/auth_helper.py"
    imports = _imports(path)

    assert (
        "masyg_extractor.integrations.accounting.shared.oauth_state"
        in imports
    )
    assert not any(
        module.startswith(FORBIDDEN_LEGACY_PREFIXES)
        for module in imports
    )


def test_canonical_quickbooks_live_support_owners_exist():
    expected = (
        ROOT / "masyg_extractor/integrations/accounting/shared/oauth_state.py",
        ROOT / "masyg_extractor/integrations/accounting/quickbooks/client.py",
        ROOT
        / "masyg_extractor/integrations/accounting/quickbooks/"
        "authentication/quickbook_auth.py",
        ROOT
        / "masyg_extractor/integrations/accounting/quickbooks/"
        "services/quickbook_service.py",
    )

    assert all(path.is_file() for path in expected)


def test_canonical_xero_live_support_owners_exist():
    expected = (
        ROOT / "masyg_extractor/integrations/accounting/xero/client.py",
        ROOT
        / "masyg_extractor/integrations/accounting/xero/"
        "authentication/xero_auth.py",
    )

    assert all(path.is_file() for path in expected)


def test_canonical_provider_clients_use_shared_token_owner():
    qb_client = _imports(
        ROOT / "masyg_extractor/integrations/accounting/quickbooks/client.py"
    )
    qb_service = _imports(
        ROOT
        / "masyg_extractor/integrations/accounting/quickbooks/"
        "services/quickbook_service.py"
    )
    xero_client = _imports(
        ROOT / "masyg_extractor/integrations/accounting/xero/client.py"
    )

    token_owner = (
        "masyg_extractor.integrations.accounting.shared.token_repository"
    )

    assert token_owner in qb_client
    assert token_owner in qb_service
    assert token_owner in xero_client


def test_registry_remains_on_canonical_provider_routers():
    source = (
        ROOT / "masyg_extractor/integrations/accounting/registry.py"
    ).read_text()

    assert (
        'router_module="masyg_extractor.integrations.accounting.'
        'quickbooks.router"'
        in source
    )
    assert (
        'router_module="masyg_extractor.integrations.accounting.'
        'xero.router"'
        in source
    )
