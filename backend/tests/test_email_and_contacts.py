from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.email_service import is_valid_email, send_task_assignment_email
from app.task_store import add_contact, create_task, resolve_contact_email, search_contacts
from app.task_store import add_contact, create_task, resolve_contact_email, search_contacts


def test_email_validation():
    assert is_valid_email('a@b.com')
    assert not is_valid_email('not-an-email')
    assert not is_valid_email(None)


def test_email_gracefully_skips_when_disabled():
    status = send_task_assignment_email('alice.johnson@example.com', {'title': 'x'})
    assert status['sent'] is False  # EMAIL_ENABLED=false in tests
    assert 'reason' in status


def test_create_task_resolves_seeded_contact_email():
    task = create_task('prepare quarterly report', assignee='Alice Johnson')
    assert task['assignee_email'] == 'alice.johnson@example.com'
    assert task['id'].startswith('TASK-')
    # email attempt is reported even though sending is disabled in tests
    assert 'email' in task


def test_add_contact_and_resolve():
    add_contact('Grace Hopper', 'grace.hopper@example.com', 'Engineering')
    assert resolve_contact_email('Grace Hopper') == 'grace.hopper@example.com'
    assert any(c['email'] == 'grace.hopper@example.com' for c in search_contacts('grace'))


def test_explicit_email_overrides_directory():
    task = create_task('call client', assignee='Someone New', assignee_email='new.person@example.com')
    assert task['assignee_email'] == 'new.person@example.com'


def test_task_email_includes_assignee_and_project_mailbox():
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    config = SimpleNamespace(
        email_enabled=True,
        task_notification_email='project@example.com',
        smtp_host='smtp.example.com',
        smtp_port=587,
        smtp_username='sender@example.com',
        smtp_password='app-password',
        smtp_use_tls=True,
        smtp_timeout_seconds=15,
        smtp_from='sender@example.com',
    )
    with patch('app.email_service.settings', return_value=config), \
            patch('app.email_service.smtplib.SMTP', return_value=smtp):
        status = send_task_assignment_email('alice@example.com', {'title': 'Report'})

    assert status['sent'] is True
    assert status['recipients'] == ['alice@example.com', 'project@example.com']
    assert smtp.send_message.call_count == 2
    assert [call.kwargs['to_addrs'] for call in smtp.send_message.call_args_list] == [
        ['alice@example.com'], ['project@example.com'],
    ]
