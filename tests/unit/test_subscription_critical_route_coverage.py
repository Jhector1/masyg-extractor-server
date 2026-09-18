from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
BANK = ROOT / "masyg_extractor/integrations/bank/router.py"
ACCOUNTING = ROOT / "masyg_extractor/integrations/accounting/shared/status_router.py"
EXTRACTOR = ROOT / "masyg_extractor/routes/data_extractor_routes.py"


def function_block(source: str, name: str) -> str:
    marker = re.search(
        rf"(?m)^async\s+def\s+{re.escape(name)}\s*\(",
        source,
    )
    assert marker is not None, name

    next_router = re.search(
        r"(?m)^@router\.",
        source[marker.end():],
    )
    end = (
        len(source)
        if next_router is None
        else marker.end() + next_router.start()
    )
    return source[marker.start():end]


def has_dep(block: str, dependency: str) -> bool:
    return bool(
        re.search(
            rf"Depends\(\s*{re.escape(dependency)}\s*\)",
            block,
        )
    )


def test_bank_paid_mutations_use_active_subscription_guard():
    source = BANK.read_text()

    for name in (
        "create_link_token",
        "exchange_public_token",
        "match_bank_transaction",
        "unmatch_bank_transaction",
        "update_bank_transaction_status",
        "bulk_update_bank_transaction_status",
        "create_update_link_token",
        "sync_transactions",
    ):
        assert has_dep(
            function_block(source, name),
            "require_active_subscription",
        ), name


def test_bank_reads_and_disconnect_remain_auth_only():
    source = BANK.read_text()

    for name in (
        "get_accounts",
        "get_transactions",
        "get_reconciliation",
        "disconnect_item",
    ):
        block = function_block(source, name)
        assert has_dep(block, "get_current_user_from_cookie"), name
        assert not has_dep(block, "require_active_subscription"), name


def test_accounting_paid_workflow_is_subscription_guarded():
    source = ACCOUNTING.read_text()

    for name in (
        "post_accounting_batch_preflight",
        "post_accounting_execution_plan",
        "post_accounting_execution",
        "post_accounting_verify_status",
    ):
        assert has_dep(
            function_block(source, name),
            "require_active_accounting_subscription",
        ), name


def test_accounting_reads_remain_auth_only():
    source = ACCOUNTING.read_text()

    for name in (
        "get_accounting_document_handoff",
        "get_accounting_durable_status",
    ):
        block = function_block(source, name)
        assert has_dep(block, "get_current_user_from_cookie"), name
        assert not has_dep(
            block,
            "require_active_accounting_subscription",
        ), name


def test_document_content_mutations_are_guarded():
    source = EXTRACTOR.read_text()

    for name in (
        "update_change_log",
        "update_record",
        "update_group_name",
    ):
        assert has_dep(
            function_block(source, name),
            "require_active_subscription",
        ), name


def test_document_reads_and_data_rights_remain_subscription_exempt():
    source = EXTRACTOR.read_text()

    for name in (
        "get_user_data",
        "update_view_status",
        "delete_group",
        "delete_record",
        "trash_group",
        "trash_file",
        "restore_group",
        "restore_file",
    ):
        assert not has_dep(
            function_block(source, name),
            "require_active_subscription",
        ), name
