from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUARD = "Depends(require_active_accounting_subscription)"

MUTATIONS = {
    "masyg_extractor/integrations/accounting/quickbooks/router.py": ("/send-salereceipt-in-bulk", "/send-invoice-in-bulk", "/send-invoice"),
    "masyg_extractor/integrations/accounting/xero/router.py": ("/send-invoice-in-bulk", "/send-receipt-in-bulk", "/send-invoice"),
    "masyg_extractor/integrations/accounting/shared/status_router.py": ("/execute",),
}

def route_block(source: str, endpoint: str) -> str:
    marker = f'@router.post("{endpoint}")'
    start = source.index(marker)
    next_route = source.find("\n@router.", start + len(marker))
    return source[start: len(source) if next_route < 0 else next_route]

def test_every_accounting_provider_mutation_requires_active_subscription():
    for relative, endpoints in MUTATIONS.items():
        source = (ROOT / relative).read_text()
        for endpoint in endpoints:
            assert GUARD in route_block(source, endpoint), (relative, endpoint)

def test_shared_accounting_route_subscription_classification():
    source = (
        ROOT
        / "masyg_extractor/integrations/accounting/shared/status_router.py"
    ).read_text()

    def route_block(method: str, endpoint: str) -> str:
        marker = f'@router.{method}("{endpoint}")'
        assert marker in source, endpoint
        start = source.index(marker)
        next_route = source.find("\n@router.", start + len(marker))
        return source[
            start:
            len(source) if next_route < 0 else next_route
        ]

    for endpoint in (
        "/preflight",
        "/execution-plan",
        "/execute",
        "/verify-status",
    ):
        block = route_block("post", endpoint)
        assert GUARD in block, endpoint

    for method, endpoint in (
        ("get", "/handoff"),
        ("get", "/status"),
    ):
        block = route_block(method, endpoint)
        assert GUARD not in block, endpoint
