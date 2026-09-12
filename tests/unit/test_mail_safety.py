import asyncio

from masyg_extractor.services.mail_delivery import send_message_safely


class FakeMessage:
    subject = "Test"
    recipients = ["user@example.com"]


class SuccessfulMail:
    def __init__(self):
        self.sent = False

    async def send_message(self, message):
        self.sent = True


class FailingMail:
    async def send_message(self, message):
        raise RuntimeError("provider unavailable")


def test_safe_mail_sends_normally():
    mail = SuccessfulMail()
    delivered = asyncio.run(send_message_safely(mail, FakeMessage()))
    assert delivered is True
    assert mail.sent is True


def test_safe_mail_contains_provider_exception():
    # Transactional provider failures must not escape a Starlette background task.
    delivered = asyncio.run(send_message_safely(FailingMail(), FakeMessage()))
    assert delivered is False
