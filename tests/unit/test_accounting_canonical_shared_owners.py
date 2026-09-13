import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

CANONICAL_CONTEXT = (
    "masyg_extractor.integrations.accounting.core.integration_context"
)
CANONICAL_MODELS = "masyg_extractor.integrations.accounting.core.models"
CANONICAL_REPOSITORY = (
    "masyg_extractor.integrations.accounting.shared.firestore_repository"
)

VERSIONED_OWNERS = {
    "masyg_extractor.integration_qb_v5.core.integration_context",
    "masyg_extractor.integration_v4.core.integration_context",
    "masyg_extractor.integration_qb_v5.domain.models",
    "masyg_extractor.integration_v4.domain.models",
    "masyg_extractor.integration_qb_v5.repository.firestore_repository",
    "masyg_extractor.integration_v4.repository.firestore_repository",
}

COMPATIBILITY_FILES = {
    "masyg_extractor/integration_qb_v5/core/integration_context.py",
    "masyg_extractor/integration_v4/core/integration_context.py",
    "masyg_extractor/integration_qb_v5/domain/models.py",
    "masyg_extractor/integration_v4/domain/models.py",
    "masyg_extractor/integration_qb_v5/repository/firestore_repository.py",
    "masyg_extractor/integration_v4/repository/firestore_repository.py",
}


def _imports(path: Path):
    tree = ast.parse(path.read_text(), filename=str(path))
    result = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            result.append(node.module or "")
        elif isinstance(node, ast.Import):
            result.extend(alias.name for alias in node.names)
    return result


def test_canonical_shared_owner_files_exist():
    assert (
        ROOT
        / "masyg_extractor/integrations/accounting/core/integration_context.py"
    ).is_file()
    assert (
        ROOT / "masyg_extractor/integrations/accounting/core/models.py"
    ).is_file()
    assert (
        ROOT
        / "masyg_extractor/integrations/accounting/shared/firestore_repository.py"
    ).is_file()


def test_production_code_uses_canonical_context_models_and_repository():
    offenders = []

    for path in (ROOT / "masyg_extractor").rglob("*.py"):
        relative = str(path.relative_to(ROOT))
        if relative in COMPATIBILITY_FILES:
            continue

        for module in _imports(path):
            if module in VERSIONED_OWNERS:
                offenders.append(f"{relative}: {module}")

    assert offenders == []


def test_versioned_owner_files_are_compatibility_reexports():
    expectations = {
        "masyg_extractor/integration_qb_v5/core/integration_context.py":
            CANONICAL_CONTEXT,
        "masyg_extractor/integration_v4/core/integration_context.py":
            CANONICAL_CONTEXT,
        "masyg_extractor/integration_qb_v5/domain/models.py":
            CANONICAL_MODELS,
        "masyg_extractor/integration_v4/domain/models.py":
            CANONICAL_MODELS,
        "masyg_extractor/integration_qb_v5/repository/firestore_repository.py":
            CANONICAL_REPOSITORY,
        "masyg_extractor/integration_v4/repository/firestore_repository.py":
            CANONICAL_REPOSITORY,
    }

    for relative, canonical_module in expectations.items():
        imports = _imports(ROOT / relative)
        assert canonical_module in imports, relative
