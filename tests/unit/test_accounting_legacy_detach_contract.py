import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

VERSIONED_TREES = (
    ROOT / "masyg_extractor/integration_qb_v5",
    ROOT / "masyg_extractor/integration_v4",
)

CANONICAL_PROVIDER_ROOTS = (
    ROOT / "masyg_extractor/integrations/accounting/quickbooks",
    ROOT / "masyg_extractor/integrations/accounting/xero",
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


def test_versioned_provider_trees_have_no_python_sources():
    for path in VERSIONED_TREES:
        assert not path.exists() or not any(path.rglob("*.py"))


def test_canonical_provider_roots_exist():
    assert all(path.is_dir() for path in CANONICAL_PROVIDER_ROOTS)


def test_canonical_provider_sources_have_no_versioned_imports():
    forbidden = (
        "masyg_extractor.integration_qb_v5",
        "masyg_extractor.integration_v4",
    )
    offenders = []

    for provider_root in CANONICAL_PROVIDER_ROOTS:
        for path in provider_root.rglob("*.py"):
            for module in _imports(path):
                if module.startswith(forbidden):
                    offenders.append(
                        f"{path.relative_to(ROOT)} imports {module}"
                    )

    assert offenders == []
