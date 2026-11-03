"""The channel interface every delivery medium implements."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class Notification:
    """A channel-agnostic message. Each channel renders it for its medium."""
    subject: str
    body_text: str
    body_html: str | None = None
    meta: dict = field(default_factory=dict)  # e.g. {'recipient_name': 'Alice'}


class DeliveryChannel(ABC):
    """Base class for a delivery channel.

    Subclasses implement three small methods. The public `send()` wraps them so
    every channel behaves the same way: it never raises and always returns a
    structured status dict with at least {channel, sent, reason}.
    """
    name: str = 'base'
    # True  = destination depends on the person (email address, phone) -> send one
    #         message per recipient.
    # False = destination is a fixed channel/webhook (Teams, Slack) -> post ONCE,
    #         naming the recipients, no matter how many people are chosen.
    per_recipient: bool = True

    @abstractmethod
    def is_configured(self) -> bool:
        """True when this channel has the credentials/URL it needs."""

    @abstractmethod
    def destination_for(self, contact: dict) -> str | None:
        """The address/number/URL this channel sends to for a given contact."""

    @abstractmethod
    def _send(self, destination: str, note: Notification) -> dict:
        """Actually send. May raise; `send()` catches it."""

    def send(self, contact: dict, note: Notification) -> dict:
        if not self.is_configured():
            return {'channel': self.name, 'sent': False,
                    'reason': f'{self.name} channel is not configured.'}
        destination = self.destination_for(contact)
        if not destination:
            return {'channel': self.name, 'sent': False,
                    'reason': f'No {self.name} destination for this recipient.'}
        try:
            result = self._send(destination, note)
            result.setdefault('channel', self.name)
            result.setdefault('to', destination)
            result.setdefault('sent', False)
            return result
        except Exception as exc:  # noqa: BLE001 - a channel must never break the flow
            log.warning('%s delivery failed: %s', self.name, exc)
            return {'channel': self.name, 'sent': False, 'to': destination,
                    'reason': f'{self.name} error: {exc}'}
