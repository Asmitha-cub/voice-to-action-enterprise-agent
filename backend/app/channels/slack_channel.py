"""Slack delivery channel via an Incoming Webhook.

Included to show the framework is open-ended: a brand-new channel is one small
file plus a couple of config lines. Set SLACK_WEBHOOK_URL from
https://api.slack.com/messaging/webhooks.
"""
from __future__ import annotations

import httpx

from ..config import settings
from .base import DeliveryChannel, Notification


class SlackChannel(DeliveryChannel):
    name = 'slack'
    per_recipient = False  # one webhook -> post once to the channel, naming people

    def is_configured(self) -> bool:
        c = settings()
        return bool(c.slack_enabled and c.slack_webhook_url)

    def destination_for(self, contact: dict) -> str | None:
        return settings().slack_webhook_url

    def _send(self, destination: str, note: Notification) -> dict:
        who = note.meta.get('recipient_name', '')
        header = f'*{note.subject}*' + (f' — for {who}' if who else '')
        resp = httpx.post(destination, json={'text': f'{header}\n\n{note.body_text}'},
                          timeout=settings().http_timeout_seconds)
        if resp.status_code >= 400:
            return {'sent': False, 'reason': f'Slack HTTP {resp.status_code}: {resp.text[:200]}'}
        return {'sent': True, 'reason': 'Posted to Slack channel.'}
