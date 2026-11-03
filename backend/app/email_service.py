"""Email notifications for task assignment.

When a task is created and assigned to a person, that person is emailed the
task details. The sender is intentionally *graceful*: if SMTP is not configured
the task is still created and a clear, structured status is returned instead of
raising. This keeps the demo working out-of-the-box (e.g. with Mailpit in
Docker) and lets you switch to real SMTP (Gmail, SES, etc.) by editing .env.
"""
from __future__ import annotations

import logging
import re
import smtplib
from email.message import EmailMessage

from .config import settings

log = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def is_valid_email(value: str | None) -> bool:
    return bool(value and _EMAIL_RE.match(value.strip()))


def _build_message(to_email: str, task: dict) -> EmailMessage:
    cfg = settings()
    assignee = task.get('assignee') or 'there'
    title = task.get('title', '')
    due = task.get('due_date', 'Not specified')
    priority = task.get('priority', 'normal')
    task_id = task.get('id', '')

    msg = EmailMessage()
    msg['Subject'] = f'[Task Assigned] {title}'
    msg['From'] = cfg.smtp_from
    msg['To'] = to_email

    plain = (
        f'Hi {assignee},\n\n'
        f'A new task has been assigned to you via the Enterprise Agent.\n\n'
        f'  Task:     {title}\n'
        f'  Task ID:  {task_id}\n'
        f'  Due date: {due}\n'
        f'  Priority: {priority}\n\n'
        f'This message was generated automatically after human approval of the action.\n'
    )
    msg.set_content(plain)

    html = f"""\
<html><body style="font-family:Segoe UI,Arial,sans-serif;color:#0f172a">
  <h2 style="margin:0 0 8px">📋 New task assigned to you</h2>
  <p>Hi {assignee}, a new task has been assigned to you via the Enterprise Agent.</p>
  <table style="border-collapse:collapse;margin-top:8px">
    <tr><td style="padding:4px 12px;color:#64748b">Task</td><td style="padding:4px 12px"><b>{title}</b></td></tr>
    <tr><td style="padding:4px 12px;color:#64748b">Task ID</td><td style="padding:4px 12px">{task_id}</td></tr>
    <tr><td style="padding:4px 12px;color:#64748b">Due date</td><td style="padding:4px 12px">{due}</td></tr>
    <tr><td style="padding:4px 12px;color:#64748b">Priority</td><td style="padding:4px 12px">{priority}</td></tr>
  </table>
  <p style="color:#64748b;font-size:12px;margin-top:16px">
    Generated automatically after human approval of the action.
  </p>
</body></html>"""
    msg.add_alternative(html, subtype='html')
    return msg


def smtp_send(recipients: list[str], subject: str, body_text: str,
              body_html: str | None = None) -> dict:
    """Generic SMTP sender used by the email delivery channel.

    Returns a structured status and never raises. Mirrors the graceful
    behaviour of the task-assignment email so any caller can rely on it.
    """
    cfg = settings()
    recipients = [r.strip().lower() for r in recipients if r and r.strip()]
    if not cfg.email_enabled:
        return {'sent': False, 'reason': 'Email notifications are disabled.'}
    if not recipients:
        return {'sent': False, 'reason': 'No email recipient.'}
    invalid = [r for r in recipients if not is_valid_email(r)]
    if invalid:
        return {'sent': False, 'to': ', '.join(recipients),
                'reason': f'Invalid email address: {", ".join(invalid)}'}
    if not cfg.smtp_host:
        return {'sent': False, 'to': ', '.join(recipients),
                'reason': 'SMTP is not configured (set SMTP_HOST in .env).'}
    if cfg.smtp_username and not cfg.smtp_password:
        return {'sent': False, 'to': ', '.join(recipients),
                'reason': 'SMTP_PASSWORD is missing. Gmail requires a 16-character App Password.'}
    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=cfg.smtp_timeout_seconds) as server:
            server.ehlo()
            if cfg.smtp_use_tls:
                server.starttls()
                server.ehlo()
            # Only log in if credentials are set AND the server actually offers AUTH
            # (Mailpit has no AUTH, so logging in there would raise).
            if cfg.smtp_username and cfg.smtp_password and server.has_extn('auth'):
                server.login(cfg.smtp_username, cfg.smtp_password)
            for recipient in recipients:
                msg = EmailMessage()
                msg['Subject'] = subject
                msg['From'] = cfg.smtp_from
                msg['To'] = recipient
                msg.set_content(body_text)
                if body_html:
                    msg.add_alternative(body_html, subtype='html')
                server.send_message(msg, to_addrs=[recipient])
        return {'sent': True, 'to': ', '.join(recipients),
                'reason': 'Email delivered to SMTP server.'}
    except Exception as exc:  # noqa: BLE001 - delivery must not raise
        return {'sent': False, 'to': ', '.join(recipients), 'reason': f'SMTP error: {exc}'}


def send_task_assignment_email(to_email: str | None, task: dict) -> dict:
    """Return a structured status dict; never raises."""
    cfg = settings()
    candidates = [to_email, cfg.task_notification_email]
    recipients: list[str] = []
    for candidate in candidates:
        normalized = (candidate or '').strip().lower()
        if normalized and normalized not in recipients:
            recipients.append(normalized)

    if not cfg.email_enabled:
        return {'sent': False, 'reason': 'Email notifications are disabled.'}
    if not recipients:
        return {'sent': False, 'reason': 'No email address for this assignee.'}
    invalid = [recipient for recipient in recipients if not is_valid_email(recipient)]
    if invalid:
        return {'sent': False, 'to': ', '.join(recipients),
                'reason': f'Notification email is invalid: {", ".join(invalid)}'}
    if not cfg.smtp_host:
        return {'sent': False, 'reason': 'SMTP is not configured (set SMTP_HOST in .env).',
                'to': ', '.join(recipients)}
    if cfg.smtp_username and not cfg.smtp_password:
        return {
            'sent': False,
            'to': ', '.join(recipients),
            'reason': 'SMTP_PASSWORD is missing. Gmail requires a 16-character App Password.',
        }

    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=cfg.smtp_timeout_seconds) as server:
            server.ehlo()
            if cfg.smtp_use_tls:
                server.starttls()
                server.ehlo()
            if cfg.smtp_username and cfg.smtp_password and server.has_extn('auth'):
                server.login(cfg.smtp_username, cfg.smtp_password)
            for recipient in recipients:
                message = _build_message(recipient, task)
                server.send_message(message, to_addrs=[recipient])
        log.info('Task assignment email sent to %s', ', '.join(recipients))
        return {'sent': True, 'to': ', '.join(recipients),
            'recipients': recipients, 'reason': 'Email delivered to SMTP server.'}
    except Exception as exc:  # noqa: BLE001 - notification must not break task creation
        log.warning('Failed to send task email to %s: %s', ', '.join(recipients), exc)
        error_text = str(exc)
        if ('Application-specific password required' in error_text
                or 'InvalidSecondFactor' in error_text
                or '(530,' in error_text
                or '(535,' in error_text):
            reason = 'Gmail rejected the SMTP password. Use a 16-character Gmail App Password.'
        else:
            reason = f'SMTP error: {error_text}'
        return {'sent': False, 'to': ', '.join(recipients), 'recipients': recipients,
            'reason': reason}
