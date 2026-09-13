import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
THIS_FILE = Path(__file__).resolve()


def test_legacy_integration_v2_has_no_python_sources():
    legacy = ROOT / "masyg_extractor/integration_v2"
    assert not legacy.exists() or not any(legacy.rglob("*.py"))


def test_legacy_integration_v3_has_no_python_sources():
    legacy = ROOT / "masyg_extractor/integration_v3"
    assert not legacy.exists() or not any(legacy.rglob("*.py"))


def test_production_and_tests_do_not_import_legacy_v2_or_v3():
    roots = [
        ROOT / "masyg_extractor",
        ROOT / "tests",
    ]

    forbidden = (
        "masyg_extractor.integration_v2",
        "masyg_extractor.integration_v3",
    )

    for scan_root in roots:
        for path in scan_root.rglob("*.py"):
            source = path.read_text(errors="ignore")
            tree = ast.parse(source, filename=str(path))

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for token in forbidden:
                            assert not (
                                alias.name == token
                                or alias.name.startswith(token + ".")
                            ), (
                                f"{token} import remains in "
                                f"{path.relative_to(ROOT)}:{node.lineno}"
                            )

                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    for token in forbidden:
                        assert not (
                            module == token
                            or module.startswith(token + ".")
                        ), (
                            f"{token} import remains in "
                            f"{path.relative_to(ROOT)}:{node.lineno}"
                        )


def test_dead_xero_receipt_service_is_removed():
    receipt_service = (
        ROOT
        / "masyg_extractor/integration_v4/intergrate/xero/services/receipt_service.py"
    )
    assert not receipt_service.exists()
