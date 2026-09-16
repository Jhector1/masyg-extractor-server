from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def source(relative: str) -> str:
    return (ROOT / relative).read_text()


def test_xero_adapter_normalizes_http_status_errors():
    adapter = source("masyg_extractor/integrations/accounting/xero/adapter.py")

    assert "except httpx.HTTPStatusError as exc:" in adapter
    assert "normalize_xero_http_error(exc.response)" in adapter
    assert 'normalized["status_code"]' in adapter
    assert 'normalized["error"]' in adapter


def test_xero_adapter_returns_safe_transport_error():
    adapter = source("masyg_extractor/integrations/accounting/xero/adapter.py")

    assert "except httpx.RequestError as exc:" in adapter
    assert '"Xero service unavailable. Please try again."' in adapter
    assert '"status_code": 502' in adapter
    assert '"document_errors": []' in adapter


def test_xero_adapter_does_not_recreate_error_parser():
    adapter = source("masyg_extractor/integrations/accounting/xero/adapter.py")
    client = source("masyg_extractor/integrations/accounting/xero/client.py")

    assert "def _normalize_xero_http_error" in client
    assert (
        "from masyg_extractor.integrations.accounting.xero.client "
        "import _normalize_xero_http_error as normalize_xero_http_error"
    ) in adapter
    assert "def _normalize_xero_http_error" not in adapter

def test_xero_adapter_preserves_explicit_get_params():
    adapter = source(
        "masyg_extractor/integrations/accounting/xero/adapter.py"
    )

    assert (
        "request_params = payload if payload is not None else params"
        in adapter
    )
    assert "params=request_params" in adapter
    assert 'kwargs.pop("params"' not in adapter
