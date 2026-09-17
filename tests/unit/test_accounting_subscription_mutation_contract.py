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

def test_read_only_shared_routes_are_not_subscription_gated():
    source = (ROOT / "masyg_extractor/integrations/accounting/shared/status_router.py").read_text()
    for endpoint in ("/batch-preflight", "/execution-plan", "/verify-status", "/status"):
        marker = f'@router.get("{endpoint}")' if f'@router.get("{endpoint}")' in source else f'@router.post("{endpoint}")'
        if marker not in source:
            continue
        start = source.index(marker)
        next_route = source.find("\n@router.", start + len(marker))
        block = source[start: len(source) if next_route < 0 else next_route]
        assert GUARD not in block, endpoint
