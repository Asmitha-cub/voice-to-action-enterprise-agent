"""Channel registry + dispatcher.

Register a channel once here and it is available everywhere. `deliver()` sends
one Notification to one or many channels and returns a combined, per-channel
status. To add a new medium (SMS, Telegram, ...), create one file like the
others and add its class to `_CHANNELS` below — nothing else changes.
"""
from __future__ import annotations

from ..config import settings
from .base import Notification
from .email_channel import EmailChannel
from .slack_channel import SlackChannel
from .teams_channel import TeamsChannel
from .whatsapp_channel import WhatsAppChannel

# The single place channels are registered.
_CHANNELS = {
    ch.name: ch
    for ch in (EmailChannel(), WhatsAppChannel(), TeamsChannel(), SlackChannel())
}


def available_channels() -> list[str]:
    return list(_CHANNELS)


def get_channel(name: str):
    return _CHANNELS.get((name or '').strip().lower())


def channel_status() -> list[dict]:
    """What each channel is and whether it is ready to send."""
    return [{'channel': name, 'configured': ch.is_configured()}
            for name, ch in _CHANNELS.items()]


def deliver(contact: dict, note: Notification, channels: list[str] | None = None) -> dict:
    """Send `note` to a SINGLE `contact` across the requested channels."""
    return deliver_multi([contact], note, channels)


def deliver_multi(recipients: list[dict], note: Notification,
                  channels: list[str] | None = None) -> dict:
    """Send `note` to one or MANY recipients across the requested channels.

    Per-recipient channels (email, whatsapp) send one message to each person,
    using that person's own address/phone. Broadcast channels (teams, slack)
    post ONCE to their fixed webhook, naming everyone — the webhook URL never
    changes per person.
    """
    import dataclasses

    names = channels or settings().default_channels_list
    recipients = recipients or []
    all_names = [r.get('name', '') for r in recipients if r.get('name')]
    results: list[dict] = []

    for name in names:
        channel = get_channel(name)
        if channel is None:
            results.append({'channel': name, 'sent': False, 'reason': 'Unknown channel.'})
            continue

        if channel.per_recipient:
            # One message per person (email to each address, whatsapp to each phone).
            targets = recipients or [{}]
            for contact in targets:
                r = channel.send(contact, note)
                r['recipient'] = contact.get('name', '')
                results.append(r)
        else:
            # Post once to the fixed channel/webhook, naming everyone.
            combined = dataclasses.replace(
                note, meta={**note.meta, 'recipient_name': ', '.join(all_names)})
            r = channel.send(recipients[0] if recipients else {}, combined)
            r['recipient'] = ', '.join(all_names) or 'channel'
            results.append(r)

    return {
        'requested': names,
        'recipients': all_names,
        'any_sent': any(r.get('sent') for r in results),
        'channels': results,
    }
