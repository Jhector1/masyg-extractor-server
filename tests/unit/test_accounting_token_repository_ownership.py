import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_shared_token_repository_owns_generic_read():
    path = (
        ROOT
        / "masyg_extractor/integrations/accounting/shared/token_repository.py"
    )
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))

    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "get_integration_token"
    )

    calls = list(ast.walk(function))

    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "collection"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "integrations"
        for node in calls
    )

    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "document"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "integration"
        for node in calls
    )

    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and len(node.args) >= 1
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "tokenData"
        for node in calls
    )


def test_xero_client_uses_generic_token_owner():
    source = (
        ROOT / "masyg_extractor/integrations/accounting/xero/client.py"
    ).read_text()

    assert (
        "from masyg_extractor.integrations.accounting.shared.token_repository "
        "import get_integration_token"
    ) in source
    assert "get_integration_token, user_id, \"xero\"" in source
    assert "get_quickbooks_token" not in source


def test_quickbooks_client_uses_generic_token_owner():
    source = (
        ROOT / "masyg_extractor/integrations/accounting/quickbooks/client.py"
    ).read_text()

    assert (
        "from masyg_extractor.integrations.accounting.shared.token_repository "
        "import get_integration_token as get_quickbooks_token"
    ) in source
    assert "get_quickbooks_token" in source
    assert '"quickbooks"' in source
