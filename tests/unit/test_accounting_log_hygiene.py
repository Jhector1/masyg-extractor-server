from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


ACCOUNTING_FILES = (
    ROOT / "masyg_extractor/integrations/accounting/quickbooks/entity_helper.py",
    ROOT / "masyg_extractor/integrations/accounting/quickbooks/services/customer_service.py",
    ROOT / "masyg_extractor/integrations/accounting/quickbooks/services/item_service.py",
    ROOT / "masyg_extractor/integrations/accounting/xero/entity_helper.py",
    ROOT / "masyg_extractor/integrations/accounting/xero/services/customer_service.py",
    ROOT / "masyg_extractor/integrations/accounting/xero/services/item_service.py",
)


FORBIDDEN_LOG_SNIPPETS = (
    "Payload for creating customer:",
    "Creating item with payload:",
    "Creating bulk items with payload:",
    "Bulk customer creation merged payload:",
    "Merging current entities:",
    'response received: {response}',
    'response: {resp}',
)


def test_accounting_info_logs_do_not_dump_business_payloads_or_provider_responses():
    combined = "\n".join(path.read_text() for path in ACCOUNTING_FILES)

    for snippet in FORBIDDEN_LOG_SNIPPETS:
        assert snippet not in combined


def test_shared_logger_does_not_install_a_private_stream_handler():
    source = (
        ROOT / "masyg_extractor/services/my_log.py"
    ).read_text()

    assert "logger.addHandler(" not in source
    assert "logging.StreamHandler()" not in source
    assert "logger.propagate = True" in source


def test_socket_log_content_is_not_duplicated_into_local_info_logs():
    source = (
        ROOT / "masyg_extractor/services/my_log.py"
    ).read_text()

    assert "Queued log: {message}" not in source
    assert 'logger.debug("Queued Socket.IO log event")' in source


def test_xero_legacy_invoicess_remains_read_compatibility_only():
    durable_source = (
        ROOT
        / "masyg_extractor/integrations/accounting/shared/durable_status.py"
    ).read_text()

    service_source = (
        ROOT
        / "masyg_extractor/integrations/accounting/xero/services/document_service.py"
    ).read_text()

    assert 'legacy_record_types=("invoicess",)' in durable_source
    assert "_xero_legacy_accounting_record_type" in service_source

    write_tokens = (
        'store_record("invoicess"',
        'claim_record("invoicess"',
        'finalize_record("invoicess"',
        'prepare_provider_dispatch("invoicess"',
        'mark_provider_dispatch_started("invoicess"',
    )

    accounting_root = ROOT / "masyg_extractor/integrations/accounting"
    combined = "\n".join(
        path.read_text()
        for path in accounting_root.rglob("*.py")
    )

    for token in write_tokens:
        assert token not in combined



def test_entity_helper_errors_do_not_embed_full_provider_responses():
    quickbooks = (
        ROOT
        / "masyg_extractor/integrations/accounting/quickbooks/entity_helper.py"
    ).read_text()

    xero = (
        ROOT
        / "masyg_extractor/integrations/accounting/xero/entity_helper.py"
    ).read_text()

    assert "{resp}" not in quickbooks
    assert "{response}" not in xero
