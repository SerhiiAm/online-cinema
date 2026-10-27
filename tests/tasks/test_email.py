from collections.abc import Generator
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import settings
from app.tasks.email import send_activation_email

EMAIL = "user+test@example.com"
TOKEN = "test-token"


@pytest.fixture
def smtp_mock() -> Generator[MagicMock, None, None]:
    with patch("app.tasks.email.smtplib.SMTP") as mock:
        yield mock


def sent_message(smtp_mock: MagicMock) -> EmailMessage:
    smtp = smtp_mock.return_value.__enter__.return_value
    smtp.send_message.assert_called_once()
    return smtp.send_message.call_args.args[0]


def test_send_activation_email_uses_smtp_settings(smtp_mock: MagicMock) -> None:
    send_activation_email(EMAIL, TOKEN)

    smtp_mock.assert_called_once_with(settings.smtp_host, settings.smtp_port)


def test_send_activation_email_builds_message(smtp_mock: MagicMock) -> None:
    send_activation_email(EMAIL, TOKEN)

    message = sent_message(smtp_mock)
    assert message["To"] == EMAIL
    assert message["From"] == settings.email_from
    assert message["Subject"] == "Activate your account"

    body = message.get_content()
    expected_link = (
        f"{settings.frontend_url}/activate"
        f"?email=user%2Btest%40example.com&token={TOKEN}"
    )
    assert expected_link in body
    assert f"Activation token: {TOKEN}" in body
    assert f"{settings.activation_token_expire_hours} hours" in body
