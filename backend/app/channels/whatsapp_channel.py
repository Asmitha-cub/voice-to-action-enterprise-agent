"""WhatsApp delivery channel via the Twilio API.

Set up (free sandbox): https://www.twilio.com/console → Messaging → Try WhatsApp.
You get an Account SID, an Auth Token, and a sender like 'whatsapp:+14155238886'.
The recipient's number is taken from the contact's `phone` field (E.164, e.g.
'+919876543210').
"""
from __future__ import annotations

import httpx

from ..config import settings
from .base import DeliveryChannel, Notification


def _wa(number: str) -> str:
    number = number.strip()
    return number if number.startswith('whatsapp:') else f'whatsapp:{number}'


class WhatsAppChannel(DeliveryChannel):
    name = 'whatsapp'

    def is_configured(self) -> bool:
        c = settings()
        return bool(c.whatsapp_enabled and c.twilio_account_sid
                    and c.twilio_auth_token and c.twilio_whatsapp_from)

    def destination_for(self, contact: dict) -> str | None:
        phone = (contact.get('phone') or '').strip()
        return phone or None

    def _send(self, destination: str, note: Notification) -> dict:
        c = settings()
        url = f'https://api.twilio.com/2010-04-01/Accounts/{c.twilio_account_sid}/Messages.json'
        body = f'*{note.subject}*\n\n{note.body_text}'
        resp = httpx.post(
            url,
            data={'From': _wa(c.twilio_whatsapp_from), 'To': _wa(destination), 'Body': body},
            auth=(c.twilio_account_sid, c.twilio_auth_token),
            timeout=c.http_timeout_seconds,
        )
        if resp.status_code >= 400:
            return {'sent': False, 'reason': f'Twilio HTTP {resp.status_code}: {resp.text[:200]}'}
        sid = ''
        try:
            sid = resp.json().get('sid', '')
        except Exception:  # noqa: BLE001
            pass
        return {'sent': True, 'reason': 'WhatsApp message queued via Twilio.', 'provider_id': sid}
