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
