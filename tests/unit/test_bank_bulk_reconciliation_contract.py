from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text()


def test_bulk_reconciliation_has_one_authenticated_server_route():
    router = source(
        "masyg_extractor/integrations/bank/router.py"
    )

    assert (
        '@router.post("/transactions/bulk-status")'
        in router
    )
    assert (
        "BulkUpdateBankTransactionStatusRequest"
        in router
    )
    assert (
        "get_current_user_from_cookie"
        in router
    )
    assert ".bulk_set_status(" in router


def test_bulk_reconciliation_reuses_set_status_owner():
    reconciliation = source(
        "masyg_extractor/integrations/bank/reconciliation.py"
    )

    start = reconciliation.index(
        "    async def bulk_set_status("
    )
    end = reconciliation.index(
        "    async def unmatch_transaction(",
        start,
    )

    block = reconciliation[start:end]

    assert "await self.set_status(" in block
    assert (
        "MANUAL_RECONCILIATION_STATUSES"
        in block
    )

    # The orchestration layer must not invent another
    # Firestore/reconciliation persistence path.
    assert (
        "update_transaction_reconciliation"
        not in block
    )
    assert "self.repository." not in block


def test_bulk_status_cannot_bulk_match_documents():
    reconciliation = source(
        "masyg_extractor/integrations/bank/reconciliation.py"
    )

    assert (
        "ReconciliationStatus.MATCHED.value"
        not in reconciliation[
            reconciliation.index(
                "MANUAL_RECONCILIATION_STATUSES"
            ):
            reconciliation.index(
                "def _utc_now_iso"
            )
        ]
    )


def test_existing_repository_remains_claim_owner():
    repository = source(
        "masyg_extractor/integrations/bank/repository.py"
    )

    assert (
        "def update_transaction_reconciliation("
        in repository
    )
    assert (
        '.collection("reconciliationClaims")'
        in repository
    )
    assert "@firestore.transactional" in repository
    assert (
        "transaction_ref.get(transaction=txn)"
        in repository
    )


def test_bulk_route_is_bounded():
    router = source(
        "masyg_extractor/integrations/bank/router.py"
    )

    model_start = router.index(
        "class BulkUpdateBankTransactionStatusRequest"
    )
    model_end = router.index(
        "\n\ndef _user_id",
        model_start,
    )
    block = router[model_start:model_end]

    assert "min_length=1" in block
    assert "max_length=100" in block
