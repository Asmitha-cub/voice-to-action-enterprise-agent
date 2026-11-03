"""Microsoft Teams delivery channel via a Workflows (Power Automate) webhook.

Microsoft retired the classic "Incoming Webhook" connector, so the current way is
a Workflow:
  Teams channel -> ... -> Workflows -> template
  "Send webhook alerts to a chat" (or "Post to a channel when a webhook request is
  received") -> copy the URL into TEAMS_WEBHOOK_URL.

That Workflows webhook expects a Teams message containing an Adaptive Card (not the
old MessageCard), which is what this channel sends. The workflow must be turned ON
in Power Automate, or Teams returns "WorkflowTriggerIsNotEnabled".
"""
from __future__ import annotations

import httpx

from ..config import settings
from .base import DeliveryChannel, Notification


class TeamsChannel(DeliveryChannel):
    name = 'teams'
    per_recipient = False  # one webhook -> post once to the channel, naming people

    def is_configured(self) -> bool:
        c = settings()
        return bool(c.teams_enabled and c.teams_webhook_url)

    def destination_for(self, contact: dict) -> str | None:
        # A Teams webhook targets a channel/chat, not a person, so the destination
        # is the configured workflow URL for every recipient.
        return settings().teams_webhook_url

    def _send(self, destination: str, note: Notification) -> dict:
        who = note.meta.get('recipient_name', '')
        body = [
            {'type': 'TextBlock', 'size': 'Large', 'weight': 'Bolder',
             'text': note.subject, 'wrap': True},
        ]
        if who:
            body.append({'type': 'TextBlock', 'text': f'To: {who}',
                         'isSubtle': True, 'wrap': True})
        body.append({'type': 'TextBlock', 'text': note.body_text, 'wrap': True})

        payload = {
            'type': 'message',
            'attachments': [{
                'contentType': 'application/vnd.microsoft.card.adaptive',
                'content': {
                    '$schema': 'http://adaptivecards.io/schemas/adaptive-card.json',
                    'type': 'AdaptiveCard',
                    'version': '1.4',
                    'body': body,
                },
            }],
        }
        resp = httpx.post(destination, json=payload, timeout=settings().http_timeout_seconds)
        if resp.status_code >= 400:
            return {'sent': False, 'reason': f'Teams HTTP {resp.status_code}: {resp.text[:200]}'}
        return {'sent': True, 'reason': 'Posted to Microsoft Teams (Workflows webhook).'}
