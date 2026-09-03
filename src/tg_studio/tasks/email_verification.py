import logging
import smtplib
from email.message import EmailMessage

from tg_studio.config import settings
from tg_studio.modules.identity.email.templates import verification_email_html
from tg_studio.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="send_verification_email", bind=True, max_retries=3)
def send_verification_email_task(self, to_email: str, token: str, first_name: str) -> None:
    if not settings.smtp_user or not settings.smtp_password:
        logger.warning("SMTP not configured, skipping verification email to %s", to_email)
        return

    base = (settings.public_url or "").rstrip("/")
    if not base:
        logger.warning("PUBLIC_URL not set, skipping verification email to %s", to_email)
        return

    verify_url = f"{base}/api/auth/verify-email?token={token}"
    html = verification_email_html(verify_url, first_name)

    msg = EmailMessage()
    msg["Subject"] = "Confirm your email — TG Studio"
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_user}>"
    msg["To"] = to_email
    msg.set_content(
        "Please open this message in an HTML-capable email client.",
        subtype="plain",
    )
    msg.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password.replace(" ", ""))
            server.send_message(msg)
    except Exception as exc:
        logger.exception("Failed to send verification email to %s", to_email)
        raise self.retry(exc=exc, countdown=60) from exc
