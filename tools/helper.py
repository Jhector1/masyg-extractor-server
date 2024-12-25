from flask_mail import Mail
import os
mail = Mail()


def init_mail(app):
    app.config.update(
        MAIL_SERVER='smtp.gmail.com',
        MAIL_PORT=587,
        MAIL_USE_TLS=True,
        MAIL_USERNAME=  os.getenv('GMAIL_USERNAME'),
        MAIL_PASSWORD= os.getenv('GMAIL_PASSWORD'),
        MAIL_DEFAULT_SENDER= os.getenv('GMAIL_USERNAME'),
    )
    mail.init_app(app)
