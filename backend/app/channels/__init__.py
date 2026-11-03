"""Pluggable notification/delivery channels.

Every channel (email, WhatsApp, Teams, Slack, ...) implements the same
`DeliveryChannel` interface, so delivering through a new medium is a small,
isolated file instead of a change spread across the codebase. The registry
dispatches one message to one or many channels and returns a per-channel
status. All sends are *graceful*: a channel that is not configured reports a
clear reason instead of raising, so adding channels never breaks a flow.
"""
from .base import DeliveryChannel, Notification
from .registry import (
    available_channels,
    channel_status,
    deliver,
    deliver_multi,
    get_channel,
)

__all__ = [
    'DeliveryChannel', 'Notification',
    'available_channels', 'channel_status', 'deliver', 'deliver_multi', 'get_channel',
]
