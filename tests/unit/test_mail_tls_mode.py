from types import SimpleNamespace

import pytest

from masyg_extractor.services import helper


def _build_config(
    monkeypatch,
    *,
    port: str,
    starttls: str | None = None,
    ssl_tls: str | None = None,
):
    captured = {}

    monkeypatch.setenv(
        "BREVO_USERNAME",
        "smtp-user",
    )
    monkeypatch.setenv(
        "BREVO_PASSWORD",
        "smtp-password",
    )
    monkeypatch.setenv(
        "MAIL_FROM",
        "support@example.com",
    )
    monkeypatch.setenv(
        "BREVO_SMTP",
        "smtp.example.com",
    )
    monkeypatch.setenv(
        "BREVO_SMTP_PORT",
        port,
    )

    if starttls is None:
        monkeypatch.delenv(
            "MAIL_STARTTLS",
            raising=False,
        )
    else:
        monkeypatch.setenv(
            "MAIL_STARTTLS",
            starttls,
        )

    if ssl_tls is None:
        monkeypatch.delenv(
            "MAIL_SSL_TLS",
            raising=False,
        )
    else:
        monkeypatch.setenv(
            "MAIL_SSL_TLS",
            ssl_tls,
        )

    monkeypatch.setattr(
        helper,
        "configure_tls_ca_bundle",
        lambda: None,
    )

    def fake_connection_config(
        **kwargs,
    ):
        captured.update(kwargs)
        return SimpleNamespace(
            **kwargs,
        )

    monkeypatch.setattr(
        helper,
        "ConnectionConfig",
        fake_connection_config,
    )

    class DummyFastMail:
        def __init__(
            self,
            config,
        ):
            self.config = config

    monkeypatch.setattr(
        helper,
        "FastMail",
        DummyFastMail,
    )

    app = SimpleNamespace(
        state=SimpleNamespace(),
    )

    helper.init_mail(app)

    return captured


def test_port_465_defaults_to_implicit_tls(
    monkeypatch,
):
    config = _build_config(
        monkeypatch,
        port="465",
    )

    assert config[
        "MAIL_PORT"
    ] == 465

    assert config[
        "MAIL_SSL_TLS"
    ] is True

    assert config[
        "MAIL_STARTTLS"
    ] is False


@pytest.mark.parametrize(
    "port",
    [
        "587",
        "2525",
    ],
)
def test_submission_ports_default_to_starttls(
    monkeypatch,
    port,
):
    config = _build_config(
        monkeypatch,
        port=port,
    )

    assert config[
        "MAIL_PORT"
    ] == int(port)

    assert config[
        "MAIL_STARTTLS"
    ] is True

    assert config[
        "MAIL_SSL_TLS"
    ] is False


def test_explicit_tls_mode_override_is_supported(
    monkeypatch,
):
    config = _build_config(
        monkeypatch,
        port="587",
        starttls="false",
        ssl_tls="true",
    )

    assert config[
        "MAIL_STARTTLS"
    ] is False

    assert config[
        "MAIL_SSL_TLS"
    ] is True


def test_conflicting_tls_modes_fail_closed(
    monkeypatch,
):
    with pytest.raises(
        RuntimeError,
        match=(
            "MAIL_STARTTLS and "
            "MAIL_SSL_TLS cannot both be enabled"
        ),
    ):
        _build_config(
            monkeypatch,
            port="465",
            starttls="true",
            ssl_tls="true",
        )
