import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

CANONICAL_SHARED_FILES = (
    ROOT / "masyg_extractor/integrations/accounting/core/integration_context.py",
    ROOT / "masyg_extractor/integrations/accounting/core/models.py",
    ROOT / "masyg_extractor/integrations/accounting/shared/firestore_repository.py",
)

VERSIONED_TREES = (
    ROOT / "masyg_extractor/integration_qb_v5",
    ROOT / "masyg_extractor/integration_v4",
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


def test_canonical_shared_owner_files_exist():
    assert all(path.is_file() for path in CANONICAL_SHARED_FILES)


def test_versioned_accounting_trees_have_no_python_sources():
    for path in VERSIONED_TREES:
        assert not path.exists() or not any(path.rglob("*.py"))


def test_canonical_providers_import_only_canonical_shared_owners():
    provider_roots = (
        ROOT / "masyg_extractor/integrations/accounting/quickbooks",
        ROOT / "masyg_extractor/integrations/accounting/xero",
    )
    forbidden = (
        "masyg_extractor.integration_qb_v5",
        "masyg_extractor.integration_v4",
    )

    offenders = []

    for provider_root in provider_roots:
        for path in provider_root.rglob("*.py"):
            for module in _imports(path):
                if module.startswith(forbidden):
                    offenders.append(
                        f"{path.relative_to(ROOT)} imports {module}"
                    )

    assert offenders == []
