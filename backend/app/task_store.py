"""Enterprise data operations, backed entirely by the database.

Contacts, tickets and tasks are read from and written to PostgreSQL (or SQLite
in tests). `create_task` also resolves the assignee's email from the contacts
directory and sends them a notification.
"""
import uuid

from sqlalchemy import func, select

from .channels import Notification, channel_status, deliver, deliver_multi
from .config import settings
from .db import Contact, GeneratedDocument, Task, Ticket, session_scope
from .documents import generate_document
from .email_service import is_valid_email, send_task_assignment_email


# ---------------------------------------------------------------- serializers
def _task_dict(t: Task) -> dict:
    return {
        'id': t.public_id,
        'title': t.title,
        'assignee': t.assignee,
        'assignee_email': t.assignee_email,
        'due_date': t.due_date,
        'priority': t.priority,
        'status': t.status,
        'notified': t.notified,
        'notification_status': t.notification_status,
        'created_at': t.created_at.isoformat() if t.created_at else None,
    }


def _contact_dict(c: Contact) -> dict:
    return {'name': c.name, 'team': c.team, 'email': c.email,
            'phone': getattr(c, 'phone', None)}


def _ticket_dict(t: Ticket) -> dict:
    return {'id': t.public_id, 'title': t.title, 'severity': t.severity,
            'status': t.status, 'owner': t.owner}


# ------------------------------------------------------------------- contacts
def list_contacts() -> list[dict]:
    with session_scope() as db:
        rows = db.scalars(select(Contact).order_by(Contact.name)).all()
        return [_contact_dict(c) for c in rows]


def search_contacts(query: str) -> list[dict]:
    q = (query or '').strip().lower()
    if not q:
        return []
    with session_scope() as db:
        rows = db.scalars(select(Contact).order_by(Contact.name)).all()
        return [_contact_dict(c) for c in rows
                if q in f'{c.name} {c.team} {c.email}'.lower()]


def add_contact(name: str, email: str, team: str = 'General', phone: str = '') -> dict:
    name = (name or '').strip()
    email = (email or '').strip().lower()
    phone = (phone or '').strip()
    if not name:
        raise ValueError('Contact name is required.')
    if not is_valid_email(email):
        raise ValueError('A valid contact email is required.')
    with session_scope() as db:
        existing = db.scalar(select(Contact).where(func.lower(Contact.email) == email))
        if existing:
            existing.name = name
            existing.team = team or existing.team
            if phone:
                existing.phone = phone
            return _contact_dict(existing)
        contact = Contact(name=name, email=email, team=team or 'General', phone=phone or None)
        db.add(contact)
        db.flush()
        return _contact_dict(contact)


def resolve_contact(name: str) -> dict | None:
    """Return the best-matching contact (as a dict) for a given name."""
    name_l = (name or '').strip().lower()
    if not name_l or name_l in {'unassigned', 'me', 'myself'}:
        return None
    with session_scope() as db:
        rows = db.scalars(select(Contact)).all()
        for c in rows:
            if c.name.lower() == name_l:
                return _contact_dict(c)
        for c in rows:
            first = c.name.lower().split()[0]
            if first == name_l or name_l in c.name.lower() or c.name.lower() in name_l:
                return _contact_dict(c)
    return None


def resolve_contact_email(assignee: str) -> str | None:
    """Find the best email for a named assignee, dynamically, from contacts."""
    name = (assignee or '').strip().lower()
    if not name or name in {'unassigned', 'me', 'myself'}:
        return None
    with session_scope() as db:
        rows = db.scalars(select(Contact)).all()
        # exact match first, then first-name / substring match
        for c in rows:
            if c.name.lower() == name:
                return c.email
        for c in rows:
            first = c.name.lower().split()[0]
            if first == name or name in c.name.lower() or c.name.lower() in name:
                return c.email
    return None


# ---------------------------------------------------------------------- tasks
def create_task(title: str, assignee: str = 'Unassigned', due_date: str = 'Not specified',
                priority: str = 'normal', assignee_email: str = '') -> dict:
    title = (title or '').strip()
    if not title:
        raise ValueError('Task title cannot be empty.')

    assignee = (assignee or 'Unassigned').strip() or 'Unassigned'
    email = (assignee_email or '').strip().lower()
    if not is_valid_email(email):
        email = resolve_contact_email(assignee) or ''

    task = Task(
        public_id='TASK-' + uuid.uuid4().hex[:8].upper(),
        title=title,
        assignee=assignee,
        assignee_email=email or None,
        due_date=(due_date or 'Not specified').strip() or 'Not specified',
        priority=(priority or 'normal').strip().lower() or 'normal',
        status='open',
    )
    with session_scope() as db:
        db.add(task)
        db.flush()
        payload = _task_dict(task)

    # Send the assignment email (never raises; returns a structured status).
    email_status = send_task_assignment_email(email or None, payload)
    with session_scope() as db:
        row = db.scalar(select(Task).where(Task.public_id == payload['id']))
        if row is not None:
            row.notified = bool(email_status.get('sent'))
            row.notification_status = email_status.get('reason')
            payload = _task_dict(row)

    payload['email'] = email_status
    return payload


def list_tasks(limit: int = 100) -> list[dict]:
    with session_scope() as db:
        rows = db.scalars(select(Task).order_by(Task.created_at.desc()).limit(limit)).all()
        return [_task_dict(t) for t in rows]


# -------------------------------------------------------------------- tickets
def list_tickets() -> list[dict]:
    with session_scope() as db:
        rows = db.scalars(select(Ticket).order_by(Ticket.severity)).all()
        return [_ticket_dict(t) for t in rows]


def get_ticket_summary() -> dict:
    with session_scope() as db:
        tickets = [_ticket_dict(t) for t in db.scalars(select(Ticket)).all()]
    serious = [t for t in tickets if t['severity'] in {'critical', 'high'}]
    open_tickets = [t for t in tickets if t['status'] == 'open']
    return {
        'total_tickets': len(tickets),
        'open_tickets': len(open_tickets),
        'serious_ticket_count': len(serious),
        'serious_tickets': serious,
    }


# ----------------------------------------------- compose a document & deliver it
def _parse_channels(channels: str) -> list[str] | None:
    names = [c.strip().lower() for c in (channels or '').split(',') if c.strip()]
    return names or None


import re as _re


def _gmail_lookup(name: str) -> str | None:
    """Look a name up in the user's Gmail (best-effort). Imported lazily so the
    app works even if the module/network is unavailable."""
    try:
        from .mail_lookup import lookup_email_by_name
        return lookup_email_by_name(name)
    except Exception:  # noqa: BLE001
        return None


# Matches a bare email, or "Display Name <email@host>".
_BARE_EMAIL = _re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_NAME_EMAIL = _re.compile(r'^\s*(?P<name>.+?)\s*<\s*(?P<email>[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+)\s*>\s*$')


def _resolve_recipients(recipient: str) -> list[dict]:
    """Turn a comma/semicolon list into contact dicts — registered OR not.

    Each item can be:
      - a registered contact name      -> looked up in the directory
      - a raw email 'john@acme.com'     -> sent directly (name = 'john')
      - 'John Smith <john@acme.com>'    -> name + email, sent directly
    An email given for an unregistered person is auto-saved as a contact, so
    next time you can reach them by name alone.
    """
    raw = [p.strip() for p in (recipient or '').replace(';', ',').split(',') if p.strip()]
    if not raw:
        return []
    out: list[dict] = []
    seen: set[str] = set()

    for item in raw:
        contact: dict | None = None

        m = _NAME_EMAIL.match(item)
        if m:  # "Name <email>"
            name, email = m.group('name').strip(), m.group('email').strip().lower()
            contact = resolve_contact(name) or {'name': name, 'email': email, 'phone': ''}
            if not contact.get('email'):
                contact['email'] = email
            _remember_contact(name, email)
        elif _BARE_EMAIL.match(item):  # a plain email address
            email = item.strip().lower()
            existing = _contact_by_email(email)
            if existing:
                contact = existing
            else:
                name = email.split('@')[0].replace('.', ' ').replace('_', ' ').title()
                contact = {'name': name, 'email': email, 'phone': ''}
                _remember_contact(name, email)
        else:  # a name -> directory first, then (optionally) the user's Gmail
            contact = resolve_contact(item)
            if not contact:
                found = _gmail_lookup(item)
                if found:
                    contact = {'name': item, 'email': found, 'phone': ''}
                    _remember_contact(item, found)
                else:
                    contact = {'name': item, 'email': '', 'phone': ''}

        key = (contact.get('email') or contact.get('name') or item).lower()
        if key not in seen:
            seen.add(key)
            out.append(contact)
    return out


def _contact_by_email(email: str) -> dict | None:
    email = (email or '').strip().lower()
    with session_scope() as db:
        row = db.scalar(select(Contact).where(func.lower(Contact.email) == email))
        return _contact_dict(row) if row else None


def _remember_contact(name: str, email: str) -> None:
    """Save an ad-hoc recipient so the name works next time. Never raises."""
    try:
        if is_valid_email(email) and not _contact_by_email(email):
            add_contact(name or email.split('@')[0], email, 'Ad-hoc')
    except Exception:  # noqa: BLE001 - remembering is best-effort, never block a send
        pass


def compose_and_deliver(doc_type: str, recipient: str = '', instructions: str = '',
                        channels: str = '', sender_name: str = 'Enterprise Agent') -> dict:
    """Generate a document (e.g. a leave letter) and deliver it over channels.

    This is the extensible, multi-step capability: author content, then send it
    through any combination of channels (email/WhatsApp/Teams/Slack/...). Never
    raises — delivery problems are reported per channel.
    """
    recipients = _resolve_recipients(recipient)
    names = ', '.join(c.get('name', '') for c in recipients) or (recipient or '').strip()

    doc = generate_document(doc_type, instructions, {
        'recipient_name': names,
        'sender_name': sender_name,
    })

    note = Notification(
        subject=doc['title'],
        body_text=doc['body_text'],
        body_html=doc.get('body_html'),
        meta={'recipient_name': names},
    )
    chan_list = _parse_channels(channels)
    delivery = deliver_multi(recipients, note, chan_list)

    public_id = 'DOC-' + uuid.uuid4().hex[:8].upper()
    with session_scope() as db:
        db.add(GeneratedDocument(
            public_id=public_id,
            doc_type=doc_type,
            title=doc['title'],
            recipient=names,
            body=doc['body_text'],
            channels=','.join(delivery.get('requested', [])),
            delivery=delivery,
        ))

    return {
        'document': {
            'id': public_id,
            'type': doc_type,
            'title': doc['title'],
            'recipient': names,
            'generated_by': doc.get('generated_by'),
            'body_text': doc['body_text'],
        },
        'delivery': delivery,
        'channels_status': channel_status(),
    }


def send_message(message: str, recipient: str = '', channels: str = '') -> dict:
    """Send a free-form message to a person across channels (e.g. 'send hi on whatsapp').

    Unlike compose_and_deliver this does NOT author a document — it sends the
    text as-is. Never raises; delivery is reported per channel.
    """
    message = (message or '').strip()
    if not message:
        raise ValueError('Message text cannot be empty.')

    recipients = _resolve_recipients(recipient)
    names = ', '.join(c.get('name', '') for c in recipients) or (recipient or '').strip()

    note = Notification(
        subject=f'Message from {settings().app_name}',
        body_text=message,
        meta={'recipient_name': names},
    )
    delivery = deliver_multi(recipients, note, _parse_channels(channels))

    public_id = 'MSG-' + uuid.uuid4().hex[:8].upper()
    with session_scope() as db:
        db.add(GeneratedDocument(
            public_id=public_id, doc_type='message', title='Message',
            recipient=names, body=message,
            channels=','.join(delivery.get('requested', [])), delivery=delivery,
        ))

    return {
        'message': message,
        'recipient': names,
        'delivery': delivery,
        'channels_status': channel_status(),
    }


def preview_recipients(recipient: str) -> list[dict]:
    """Resolve recipients WITHOUT sending, so the UI can confirm who it will go to.

    Returns each resolved person's name + the actual email/phone it would use, so
    a wrong same-name match is visible before anything is sent.
    """
    out = []
    for c in _resolve_recipients(recipient):
        out.append({
            'name': c.get('name', ''),
            'email': c.get('email') or '',
            'phone': c.get('phone') or '',
        })
    return out


def send_via_channel(channel: str, recipient: str = '', subject: str = '',
                     body: str = '') -> dict:
    """Send a message through ONE named channel. Backs the per-channel MCP tools.

    `channel` is 'email' | 'whatsapp' | 'teams' | 'slack'. For email/whatsapp the
    recipient's address/phone is resolved from the directory (or given directly);
    teams/slack post to their fixed webhook and don't need a recipient.
    """
    body = (body or '').strip()
    if not body:
        raise ValueError('Message body cannot be empty.')

    recipients = _resolve_recipients(recipient)
    names = ', '.join(c.get('name', '') for c in recipients) or (recipient or '').strip()
    note = Notification(
        subject=(subject or '').strip() or f'Message from {settings().app_name}',
        body_text=body,
        meta={'recipient_name': names},
    )
    delivery = deliver_multi(recipients, note, [channel])

    public_id = 'MSG-' + uuid.uuid4().hex[:8].upper()
    with session_scope() as db:
        db.add(GeneratedDocument(
            public_id=public_id, doc_type=f'message:{channel}', title=note.subject,
            recipient=names, body=body,
            channels=channel, delivery=delivery,
        ))

    return {
        'channel': channel,
        'recipient': names,
        'subject': note.subject,
        'delivery': delivery,
        'channels_status': channel_status(),
    }


def list_documents(limit: int = 50) -> list[dict]:
    with session_scope() as db:
        rows = db.scalars(
            select(GeneratedDocument).order_by(GeneratedDocument.created_at.desc()).limit(limit)
        ).all()
        return [{
            'id': d.public_id, 'type': d.doc_type, 'title': d.title,
            'recipient': d.recipient, 'channels': d.channels, 'delivery': d.delivery,
            'created_at': d.created_at.isoformat() if d.created_at else None,
        } for d in rows]
