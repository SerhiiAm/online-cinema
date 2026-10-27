import smtplib
from email.message import EmailMessage
from urllib.parse import urlencode

from app.core.celery_app import celery_app
from app.core.config import settings


def send_email(to: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = settings.email_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
        smtp.send_message(message)


# OSError covers both SMTP errors and "connection refused".
@celery_app.task(autoretry_for=(OSError,), retry_backoff=True, max_retries=3)
def send_activation_email(email: str, token: str) -> None:
    query = urlencode({"email": email, "token": token})
    link = f"{settings.frontend_url}/activate?{query}"
    hours = settings.activation_token_expire_hours

    body = (
        "Welcome to Online Cinema!\n\n"
        "To activate your account, open the link below:\n"
        f"{link}\n\n"
        f"Activation token: {token}\n\n"
        f"The link is valid for {hours} hours."
    )
    send_email(email, "Activate your account", body)
