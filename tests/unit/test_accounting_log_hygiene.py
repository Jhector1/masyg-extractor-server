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



def test_log_manager_does_not_echo_socket_content_to_local_logs():
    source = (
        ROOT / "masyg_extractor/services/log_manager.py"
    ).read_text()

    assert "Queued log: {message}" not in source
    assert 'logger.debug("Queued Socket.IO log event")' in source


def test_accounting_document_log_helpers_do_not_echo_user_messages_locally():
    quickbooks = (
        ROOT
        / "masyg_extractor/integrations/accounting/quickbooks/"
        "services/document_service.py"
    ).read_text()

    xero = (
        ROOT
        / "masyg_extractor/integrations/accounting/xero/"
        "services/document_service.py"
    ).read_text()

    for source in (quickbooks, xero):
        assert "logger.info(message)" not in source
        assert "logger.error(message)" not in source
        assert "Failed to send log: {message}" not in source
        assert "self.context.log_manager.send_log(" in source

    assert (
        '(logger.error if level.lower() == "error" else logger.info)(message)'
        not in quickbooks
    )
    assert "Failed to emit QuickBooks %s user-facing log event" in quickbooks
    assert "Failed to emit Xero %s user-facing log event" in xero


def test_log_manager_runtime_emits_once_without_local_message_copy(monkeypatch):
    import asyncio
    import logging

    import masyg_extractor.services.log_manager as log_manager_module

    class FakeSio:
        def __init__(self):
            self.calls = []

        async def emit(self, event, payload, **kwargs):
            self.calls.append((event, payload, kwargs))

    class Capture(logging.Handler):
        def __init__(self):
            super().__init__()
            self.messages = []

        def emit(self, record):
            self.messages.append(record.getMessage())

    fake_sio = FakeSio()
    monkeypatch.setattr(log_manager_module, "sio", fake_sio)

    root_logger = logging.getLogger()
    capture = Capture()
    root_logger.addHandler(capture)

    sentinel = "MASYG_LOG_MANAGER_CONTENT_SENTINEL"

    try:
        manager = log_manager_module.LogManager()
        asyncio.run(
            manager.send_log(
                sentinel,
                log_key="accounting-log-message",
                user_room="test-room",
            )
        )
    finally:
        root_logger.removeHandler(capture)

    assert len(fake_sio.calls) == 1
    assert all(
        sentinel not in message
        for message in capture.messages
    )
