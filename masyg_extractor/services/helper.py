from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi_mail import FastMail, ConnectionConfig
import logging
import os

logger = logging.getLogger("masyg.mail")
_WARNED_BAD_BREVO_USERNAME = False


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def configure_tls_ca_bundle() -> str | None:
    """Ensure Python SMTP TLS has a trustworthy CA bundle.

    Python.org macOS installations can have an empty system trust path until the
    bundled certificate installer is run. Requests already depends on certifi, but
    SMTP uses Python's default SSL context rather than Requests' CA configuration.
    Pointing SSL_CERT_FILE at certifi's Mozilla CA bundle keeps verification enabled
    and makes the behavior deterministic across macOS, containers, and CI.
    """
    configured = (os.getenv("SSL_CERT_FILE") or "").strip()
    if configured and Path(configured).is_file():
        return configured

    try:
        import certifi

        bundle = certifi.where()
    except Exception as exc:  # pragma: no cover - dependency/import failure is environment-specific
        logger.warning("Could not locate certifi CA bundle for SMTP TLS: %s", type(exc).__name__)
        return None

    if not Path(bundle).is_file():  # pragma: no cover - protects against a broken installation
        logger.warning("certifi returned a missing CA bundle path")
        return None

    os.environ["SSL_CERT_FILE"] = bundle
    return bundle


def init_mail(app: FastAPI):
    global _WARNED_BAD_BREVO_USERNAME

    username = (os.getenv("BREVO_USERNAME") or "").strip()
    password = (os.getenv("BREVO_PASSWORD") or "").strip()
    mail_from = (os.getenv("MAIL_FROM") or "").strip()
    server = (os.getenv("BREVO_SMTP") or "smtp-relay.brevo.com").strip()

    missing = [
        name
        for name, value in {
            "BREVO_USERNAME": username,
            "BREVO_PASSWORD": password,
            "MAIL_FROM": mail_from,
            "BREVO_SMTP": server,
        }.items()
        if not value
    ]
    if missing:
        logger.error("Transactional email is not fully configured; missing %s", ", ".join(missing))

    # Brevo SMTP logins can legitimately be either the Brevo account login
    # email address or a generated address such as [ID]@smtp-brevo.com.
    # Do not validate the login by email suffix. The relay hostname itself,
    # however, is never the SMTP username and is a common configuration error.
    if (
        server == "smtp-relay.brevo.com"
        and username
        and username.lower() == server.lower()
        and not _WARNED_BAD_BREVO_USERNAME
    ):
        logger.warning(
            "BREVO_USERNAME is set to the SMTP relay host. "
            "Use the Login shown under Brevo Settings > SMTP & API."
        )
        _WARNED_BAD_BREVO_USERNAME = True

    validate_certs = _env_flag("MAIL_VALIDATE_CERTS", default=True)
    if os.getenv("FAST_API_ENV", "development").lower() == "production" and not validate_certs:
        raise RuntimeError("Refusing to start production with SMTP certificate validation disabled")

    if validate_certs:
        bundle = configure_tls_ca_bundle()
        if bundle:
            logger.debug("SMTP TLS CA bundle configured")

    config = ConnectionConfig(
        MAIL_USERNAME=username,
        MAIL_PASSWORD=password,
        MAIL_FROM=mail_from,
        MAIL_PORT=int(os.getenv("BREVO_SMTP_PORT", "587")),
        MAIL_SERVER=server,
        MAIL_STARTTLS=True,
        MAIL_SSL_TLS=False,
        USE_CREDENTIALS=True,
        VALIDATE_CERTS=validate_certs,
        TIMEOUT=int(os.getenv("MAIL_TIMEOUT_SECONDS", "15")),
    )
    app.state.mail = FastMail(config)
