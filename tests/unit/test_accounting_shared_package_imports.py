import subprocess
import sys


def test_operation_progress_import_does_not_require_firebase_initialization():
    code = (
        "import firebase_admin\n"
        "assert not firebase_admin._apps\n"
        "from masyg_extractor.integrations.accounting.shared.operation_progress "
        "import AccountingOperationProgress\n"
        "assert AccountingOperationProgress is not None\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_shared_package_keeps_compatibility_exports_lazy():
    import masyg_extractor.integrations.accounting.shared as shared

    assert "IntegrationTokenRepository" in shared.__all__
    assert "get_integration_token" in shared.__all__
    assert "store_integration_token" in shared.__all__
