import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

LEGACY_PROVIDER_TREES = (
    ROOT / "masyg_extractor/integrations/quickbooks",
    ROOT / "masyg_extractor/integrations/xero",
)

FORBIDDEN_IMPORT_PREFIXES = (
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


def test_legacy_provider_trees_have_no_python_sources():
    for tree in LEGACY_PROVIDER_TREES:
        assert not tree.exists() or not any(tree.rglob("*.py"))


def test_production_has_no_legacy_provider_imports():
    offenders = []

    for path in (ROOT / "masyg_extractor").rglob("*.py"):
        for module in _imports(path):
            if module.startswith(FORBIDDEN_IMPORT_PREFIXES):
                offenders.append(
                    f"{path.relative_to(ROOT)} imports {module}"
                )

    assert offenders == []
