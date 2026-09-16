from pathlib import Path

import pytest

from masyg_extractor.services import helper


def test_configure_tls_ca_bundle_uses_existing_valid_path(monkeypatch, tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_text("test")
    monkeypatch.setenv("SSL_CERT_FILE", str(ca))

    assert helper.configure_tls_ca_bundle() == str(ca)


def test_configure_tls_ca_bundle_repairs_missing_path_with_certifi(monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", "/definitely/missing/ca.pem")

    configured = helper.configure_tls_ca_bundle()

    assert configured is not None
    assert Path(configured).is_file()
    assert configured == helper.os.environ["SSL_CERT_FILE"]


def test_production_refuses_disabled_smtp_cert_validation(monkeypatch):
    from fastapi import FastAPI

    monkeypatch.setenv("FAST_API_ENV", "production")
    monkeypatch.setenv("MAIL_VALIDATE_CERTS", "0")
    monkeypatch.setenv("BREVO_USERNAME", "example@smtp-brevo.com")
    monkeypatch.setenv("BREVO_PASSWORD", "secret")
    monkeypatch.setenv("MAIL_FROM", "sender@example.com")
    monkeypatch.setenv("BREVO_SMTP", "smtp-relay.brevo.com")

    with pytest.raises(RuntimeError, match="certificate validation disabled"):
        helper.init_mail(FastAPI())



def test_brevo_account_email_login_does_not_warn(monkeypatch, caplog):
    """An account email may legitimately be the Brevo SMTP login."""
    from fastapi import FastAPI

    monkeypatch.setattr(helper, "_WARNED_BAD_BREVO_USERNAME", False)
    monkeypatch.setenv("BREVO_USERNAME", "account@example.com")
    monkeypatch.setenv("BREVO_PASSWORD", "smtp-key")
    monkeypatch.setenv("MAIL_FROM", "sender@example.com")
    monkeypatch.setenv("BREVO_SMTP", "smtp-relay.brevo.com")

    with caplog.at_level("WARNING", logger="masyg.mail"):
        helper.init_mail(FastAPI())

    assert not any(
        "BREVO_USERNAME" in record.getMessage()
        for record in caplog.records
    )


def test_brevo_relay_host_used_as_login_warns(monkeypatch, caplog):
    """The SMTP relay hostname must not be used as the SMTP login."""
    from fastapi import FastAPI

    monkeypatch.setattr(helper, "_WARNED_BAD_BREVO_USERNAME", False)
    monkeypatch.setenv("BREVO_USERNAME", "smtp-relay.brevo.com")
    monkeypatch.setenv("BREVO_PASSWORD", "smtp-key")
    monkeypatch.setenv("MAIL_FROM", "sender@example.com")
    monkeypatch.setenv("BREVO_SMTP", "smtp-relay.brevo.com")

    with caplog.at_level("WARNING", logger="masyg.mail"):
        helper.init_mail(FastAPI())

    assert any(
        "SMTP relay host" in record.getMessage()
        for record in caplog.records
    )
