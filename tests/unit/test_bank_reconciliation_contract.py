from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def source(path: str) -> str:
    return (ROOT / path).read_text()


def test_reconciliation_reuses_the_existing_bank_transaction_owner():
    repository = source(
        "masyg_extractor/integrations/bank/repository.py"
    )

    assert "def get_transaction(" in repository
    assert "def update_transaction_reconciliation(" in repository
    assert "txn.update(" in repository
    assert '"reconciliation": reconciliation' in repository

    # Provider sync remains merge-based so Plaid refreshes cannot erase local
    # reconciliation metadata.
    assert ".set(transaction, merge=True)" in repository


def test_reconciliation_does_not_create_a_parallel_transaction_collection():
    reconciliation = source(
        "masyg_extractor/integrations/bank/reconciliation.py"
    )

    assert '.collection("groups")' in reconciliation
    assert '.collection("files")' in reconciliation

    assert '.collection("reconciliation")' not in reconciliation
    assert '.collection("bank_transactions")' not in reconciliation


def test_reconciliation_reuses_canonical_document_accounting_intent():
    reconciliation = source(
        "masyg_extractor/integrations/bank/reconciliation.py"
    )

    assert "default_accounting_intent" in reconciliation
    assert "AccountingIntent.RECONCILE_EXPENSE" in reconciliation
    assert "AccountingIntent.CREATE_AP_BILL" in reconciliation
    assert "AccountingIntent.CREATE_AR_INVOICE" in reconciliation


def test_reconciliation_reuses_dashboard_line_item_math():
    reconciliation = source(
        "masyg_extractor/integrations/bank/reconciliation.py"
    )

    assert (
        "from masyg_extractor.services.analytics import as_number, line_total"
        in reconciliation
    )
    assert "total += line_total(row)" in reconciliation


def test_reconciliation_api_is_owned_by_the_existing_bank_router():
    router = source(
        "masyg_extractor/integrations/bank/router.py"
    )

    assert '@router.get("/reconciliation")' in router
    assert '@router.post("/transactions/{transaction_id}/match")' in router
    assert '@router.post("/transactions/{transaction_id}/unmatch")' in router
    assert '@router.post("/transactions/{transaction_id}/status")' in router


def test_matched_state_can_only_be_created_by_the_match_workflow():
    reconciliation = source(
        "masyg_extractor/integrations/bank/reconciliation.py"
    )

    assert "MANUAL_RECONCILIATION_STATUSES" in reconciliation
    assert 'ReconciliationStatus.MATCHED.value' in reconciliation
    assert '"group_id": group_id' in reconciliation
    assert '"file_id": file_id' in reconciliation


def test_no_silent_auto_confirmation_of_suggested_documents():
    reconciliation = source(
        "masyg_extractor/integrations/bank/reconciliation.py"
    )

    candidate_block = reconciliation[
        reconciliation.index("def _candidate_documents("):
        reconciliation.index("async def reconciliation(")
    ]

    assert "update_transaction_reconciliation" not in candidate_block
    assert '"confidence"' in candidate_block


def test_provider_transfer_is_derived_without_overwriting_provider_facts():
    reconciliation = source(
        "masyg_extractor/integrations/bank/reconciliation.py"
    )

    assert "is_provider_transfer(transaction)" in reconciliation
    assert '"source": "provider"' in reconciliation

def test_document_match_uniqueness_is_owned_by_bank_repository_transaction():
    repository = source(
        "masyg_extractor/integrations/bank/repository.py"
    )

    assert '.collection("reconciliationClaims")' in repository
    assert "@firestore.transactional" in repository
    assert "transaction_ref.get(transaction=txn)" in repository
    assert "new_claim_ref.get(" in repository
    assert "txn.set(" in repository
    assert "txn.update(" in repository
    assert (
        "Document is already matched to another "
        in repository
    )


def test_unmatch_releases_only_the_current_transactions_document_claim():
    repository = source(
        "masyg_extractor/integrations/bank/repository.py"
    )

    assert "txn.delete(old_claim_ref)" in repository
    assert 'owner.get("itemId")' in repository
    assert 'owner.get("transactionId")' in repository


def test_reconciliation_claim_is_an_index_not_a_parallel_transaction_store():
    repository = source(
        "masyg_extractor/integrations/bank/repository.py"
    )

    claim_start = repository.index(
        'claims_ref = self.bank_ref.collection("reconciliationClaims")'
    )
    claim_block = repository[
        claim_start:
        repository.index(
            "    def delete_item(",
            claim_start,
        )
    ]

    assert '"groupId"' in claim_block
    assert '"fileId"' in claim_block
    assert '"itemId"' in claim_block
    assert '"transactionId"' in claim_block

    assert '"merchant_name"' not in claim_block
    assert '"amount"' not in claim_block
    assert '"category"' not in claim_block




def test_transaction_and_item_deletion_release_document_claims():
    repository = source(
        "masyg_extractor/integrations/bank/repository.py"
    )

    assert "def _reconciliation_claim_doc_id(" in repository
    assert "def delete_transaction(" in repository
    assert "claim_ref.get(transaction=txn)" in repository
    assert "txn.delete(claim_ref)" in repository
    assert "txn.delete(transaction_ref)" in repository

    delete_item_start = repository.index(
        "    def delete_item("
    )
    delete_item_end = repository.index(
        "    @staticmethod",
        delete_item_start,
    )
    delete_item_block = repository[
        delete_item_start:delete_item_end
    ]

    assert "self.delete_transaction(" in delete_item_block
    assert '.collection("reconciliationClaims")' in delete_item_block


def test_candidates_exclude_documents_already_owned_by_a_match():
    reconciliation = source(
        "masyg_extractor/integrations/bank/reconciliation.py"
    )

    assert "claimed_document_ids" in reconciliation
    assert "excluded_document_ids=claimed_document_ids" in reconciliation
    assert "if identity in excluded:" in reconciliation


def test_known_distant_dates_are_not_reconciliation_candidates():
    reconciliation = source(
        "masyg_extractor/integrations/bank/reconciliation.py"
    )

    assert "if day_delta > 3:" in reconciliation
    assert "return None" in reconciliation

