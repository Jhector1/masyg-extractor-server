from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_server() -> str:
    return (ROOT / "server.py").read_text()


def test_maintenance_jobs_are_not_owned_by_background_poller_guards():
    server = read_server()

    maintenance = server.index(
        'id="trial_expire_daily"'
    )
    trash = server.index(
        'id="roll_failed_to_trash"'
    )
    purge = server.index(
        'id="purge_expired_trash"'
    )

    bank_guard = server.index(
        "if not pause_bank_webhook_poller:"
    )

    gmail_guard = server.index(
        "if not pause_gmail_background_jobs:"
    )

    assert maintenance < bank_guard
    assert trash < bank_guard
    assert purge < bank_guard
    assert bank_guard < gmail_guard


def test_bank_and_gmail_have_independent_circuit_breakers():
    server = read_server()

    assert '"MASYG_PAUSE_BANK_WEBHOOK_POLLER"' in server
    assert '"MASYG_PAUSE_GMAIL_BACKGROUND_JOBS"' in server

    assert server.count(
        "if not pause_bank_webhook_poller:"
    ) == 1

    assert server.count(
        "if not pause_gmail_background_jobs:"
    ) == 1


def test_high_frequency_pollers_default_paused():
    server = read_server()

    bank_env = server.index(
        '"MASYG_PAUSE_BANK_WEBHOOK_POLLER"'
    )
    gmail_env = server.index(
        '"MASYG_PAUSE_GMAIL_BACKGROUND_JOBS"'
    )

    assert '"1"' in server[
        bank_env:bank_env + 150
    ]

    assert '"1"' in server[
        gmail_env:gmail_env + 150
    ]


def test_bank_job_is_inside_bank_guard():
    server = read_server()

    guard = server.index(
        "if not pause_bank_webhook_poller:"
    )
    gmail_guard = server.index(
        "if not pause_gmail_background_jobs:"
    )

    bank_block = server[
        guard:gmail_guard
    ]

    assert "process_pending_bank_webhooks" in bank_block
    assert 'id="bank_webhook_inbox"' in bank_block


def test_gmail_jobs_are_inside_gmail_guard():
    server = read_server()

    guard = server.index(
        "if not pause_gmail_background_jobs:"
    )
    scheduler_start = server.index(
        "scheduler.start()",
        guard,
    )

    gmail_block = server[
        guard:scheduler_start
    ]

    assert "renew_gmail_watches" in gmail_block
    assert 'id="gmail_watch_renewal_daily"' in gmail_block
    assert "process_pending_gmail_notifications" in gmail_block
    assert 'id="gmail_notification_processor"' in gmail_block


def test_old_global_emergency_switch_is_not_in_release_source():
    server = read_server()

    assert (
        "MASYG_EMERGENCY_PAUSE_BACKGROUND_POLLERS"
        not in server
    )
