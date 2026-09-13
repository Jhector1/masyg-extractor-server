from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_auth_helper_uses_shared_token_repository():
    source = (
        ROOT / "masyg_extractor/integrations/auth_helper.py"
    ).read_text()

    assert (
        "from masyg_extractor.integrations.accounting.shared.token_repository "
        "import IntegrationTokenRepository"
    ) in source
    assert "QuickBooksFirestoreService" not in source
    assert "masyg_extractor.integration_v4.repository" not in source
    assert "IntegrationTokenRepository.store_integration_token_statically(" in source
    assert "IntegrationTokenRepository(user_id, self.integration)" in source


def test_shared_token_repository_owns_read_and_write_api():
    source = (
        ROOT
        / "masyg_extractor/integrations/accounting/shared/token_repository.py"
    ).read_text()

    assert "def get_integration_token(" in source
    assert "def store_integration_token(" in source
    assert "class IntegrationTokenRepository:" in source
    assert "def store_integration_token_statically(" in source


def test_no_production_code_outside_v4_imports_v4_repository():
    production = ROOT / "masyg_extractor"
    forbidden = (
        "masyg_extractor.integration_v4.repository.firestore_repository"
    )

    offenders = []
    for path in production.rglob("*.py"):
        if "integration_v4" in path.parts:
            continue
        source = path.read_text(errors="ignore")
        if forbidden in source:
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []
