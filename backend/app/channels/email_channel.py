"""Email delivery channel (SMTP), reusing the existing email service."""
from __future__ import annotations

from ..config import settings
from ..email_service import is_valid_email, smtp_send
from .base import DeliveryChannel, Notification


class EmailChannel(DeliveryChannel):
    name = 'email'

    def is_configured(self) -> bool:
        return settings().smtp_configured

    def destination_for(self, contact: dict) -> str | None:
        email = (contact.get('email') or '').strip().lower()
        return email if is_valid_email(email) else None

    def _send(self, destination: str, note: Notification) -> dict:
        return smtp_send([destination], note.subject, note.body_text, note.body_html)
