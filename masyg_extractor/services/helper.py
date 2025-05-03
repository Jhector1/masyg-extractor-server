from typing import Dict

from fastapi import FastAPI
from fastapi_mail import FastMail, ConnectionConfig
import os

def init_mail(app: FastAPI):
    config = ConnectionConfig(
        MAIL_USERNAME=os.getenv('BREVO_USERNAME'),
        MAIL_PASSWORD=os.getenv('BREVO_PASSWORD'),
        MAIL_FROM=os.getenv('MAIL_FROM'),
        MAIL_PORT=587,
        MAIL_SERVER=os.getenv('BREVO_SMTP'),
        MAIL_STARTTLS=True,    # Use MAIL_STARTTLS for STARTTLS
        MAIL_SSL_TLS=False,    # Use MAIL_SSL_TLS for SSL
        USE_CREDENTIALS=True,
        VALIDATE_CERTS=os.getenv('FAST_API_ENV')=='production'   # Disable cert validation (only for dev)
    )
    app.state.mail = FastMail(config)

