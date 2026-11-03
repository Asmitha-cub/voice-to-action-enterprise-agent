"""Tests for the pluggable delivery channels and compose-and-deliver capability."""
from app.agent import fallback_plan
from app.channels import available_channels, channel_status, deliver
from app.channels.base import Notification
from app.task_store import compose_and_deliver


def test_channels_registered():
    names = available_channels()
    assert {'email', 'whatsapp', 'teams', 'slack'} <= set(names)


def test_channel_status_shape():
    for row in channel_status():
        assert 'channel' in row and 'configured' in row
        assert isinstance(row['configured'], bool)


def test_unconfigured_channels_skip_gracefully():
    # With nothing configured in the test env, delivery must not raise and must
    # report a clear reason per channel.
    note = Notification(subject='Hi', body_text='Body')
    result = deliver({'name': 'Alice', 'email': 'a@example.com', 'phone': '+15551230001'},
                     note, ['whatsapp', 'teams', 'slack'])
    assert result['any_sent'] is False
    assert len(result['channels']) == 3
    for ch in result['channels']:
        assert ch['sent'] is False and ch['reason']


def test_fallback_plan_detects_compose_document():
    data = fallback_plan('Write a leave letter for David Kim and send it on email and whatsapp')
    assert data['action'] == 'compose_document'
    p = data['parameters']
    assert p['doc_type'] == 'leave letter'
    assert p['recipient'] == 'David Kim'
    assert set(p['channels'].split(',')) == {'email', 'whatsapp'}


def test_task_command_not_misrouted_to_compose():
    # "prepare the sales report" is a task, not a document to author.
    data = fallback_plan('Create a task to prepare the sales report for Alice by tomorrow')
    assert data['action'] == 'create_task'


def test_compose_and_deliver_generates_and_reports(_prepare_db):
    out = compose_and_deliver('leave letter', recipient='David Kim',
                              instructions='Two days of medical leave', channels='email')
    assert out['document']['title']
    assert out['document']['recipient'] == 'David Kim'
    assert out['document']['body_text']
    assert 'channels' in out['delivery']


def test_fallback_plan_detects_send_message():
    data = fallback_plan('Send hi on whatsapp to David Kim')
    assert data['action'] == 'send_message'
    p = data['parameters']
    assert p['message'].lower().startswith('hi')
    assert p['recipient'] == 'David Kim'
    assert p['channels'] == 'whatsapp'


def test_send_message_delivers_and_reports(_prepare_db):
    from app.task_store import send_message
    out = send_message('Hello there', recipient='David Kim', channels='email')
    assert out['message'] == 'Hello there'
    assert out['recipient'] == 'David Kim'
    assert out['delivery']['channels']


def test_gmail_matcher_picks_right_address():
    from app.mail_lookup import _best_match
    pairs = [('Siva Kumar Sandhi Peta', 'sivakumarnlr111@gmail.com'), ('Bob', 'bob@x.com')]
    assert _best_match(pairs, 'siva') == 'sivakumarnlr111@gmail.com'
    assert _best_match(pairs, 'Siva Kumar') == 'sivakumarnlr111@gmail.com'
    assert _best_match(pairs, 'nobody here') is None


def test_gmail_lookup_disabled_returns_none(monkeypatch):
    # Feature is off by default -> must not attempt any network call.
    from app.mail_lookup import lookup_email_by_name
    assert lookup_email_by_name('siva') is None
