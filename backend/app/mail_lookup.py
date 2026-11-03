"""Best-effort: find a person's email address in the user's own Gmail mailbox.

When a name isn't a saved contact, this searches the user's Gmail (over IMAP,
using the SAME Gmail account + app password configured for SMTP) and returns the
email address of someone whose display name matches.

Honest limits:
  * It only finds people the user has actually exchanged mail with (it reads
    message headers — From/To/Cc). It does NOT read Google Contacts.
  * Matching by display name is best-effort and can miss or mis-match.
  * Requires IMAP enabled on the Gmail account (on by default; app password works).
It never raises: returns None if disabled, unreachable, or nothing matches.
"""
from __future__ import annotations

import email.utils
import imaplib
import logging
import re
from email.parser import BytesHeaderParser

from .config import settings

log = logging.getLogger(__name__)


def _best_match(pairs: list[tuple[str, str]], query: str) -> str | None:
    """From (display_name, email) pairs, pick the address matching `query`."""
    q = query.strip().lower()
    if not q:
        return None
    tokens = [t for t in re.split(r'\s+', q) if t]
    for display, addr in pairs:
        if not addr or '@' not in addr:
            continue
        d = (display or '').lower()
        local = addr.split('@', 1)[0].lower()
        # strong: full query appears in the display name or the local-part
        if q and (q in d or q in local):
            return addr.lower()
        # weaker: every token of a multi-word name appears in the display name
        if len(tokens) > 1 and all(t in d for t in tokens):
            return addr.lower()
    # last resort: a single first-name token matching the local-part start
    for display, addr in pairs:
        if addr and addr.split('@', 1)[0].lower().startswith(q):
            return addr.lower()
    return None


def lookup_email_by_name(name: str) -> str | None:
    cfg = settings()
    name = (name or '').strip()
    if not name or not cfg.gmail_lookup_enabled:
        return None
    user, pwd = cfg.smtp_username, cfg.smtp_password
    if not (user and pwd):
        return None

    imap = None
    try:
        imap = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port, timeout=cfg.imap_timeout_seconds)
        imap.login(user, pwd)
        # All Mail covers both sent and received; fall back to INBOX.
        for mailbox in ('"[Gmail]/All Mail"', 'INBOX'):
            try:
                typ, _ = imap.select(mailbox, readonly=True)
                if typ == 'OK':
                    break
            except Exception:  # noqa: BLE001
                continue

        # Gmail raw search finds the name anywhere; cap to recent messages.
        try:
            typ, data = imap.search(None, 'X-GM-RAW', f'"{name}"')
        except Exception:  # noqa: BLE001 - non-Gmail servers lack X-GM-RAW
            typ, data = imap.search(None, 'TEXT', name)
        ids = (data[0].split() if (typ == 'OK' and data and data[0]) else [])[-40:]

        parser = BytesHeaderParser()
        for mid in reversed(ids):
            typ, msg_data = imap.fetch(mid, '(BODY.PEEK[HEADER.FIELDS (FROM TO CC)])')
            if typ != 'OK' or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            if not isinstance(raw, (bytes, bytearray)):
                continue
            headers = parser.parsebytes(bytes(raw))
            pairs = email.utils.getaddresses(
                headers.get_all('From', []) + headers.get_all('To', []) + headers.get_all('Cc', []))
            match = _best_match(pairs, name)
            if match:
                return match
    except Exception as exc:  # noqa: BLE001 - lookup must never break a send
        log.warning('Gmail lookup for %r failed: %s', name, exc)
    finally:
        try:
            if imap is not None:
                imap.logout()
        except Exception:  # noqa: BLE001
            pass
    return None
