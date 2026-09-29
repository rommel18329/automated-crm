import copy
import json
from types import SimpleNamespace

import pytest

from crm_engine import db, email_review as review
from crm_engine.gmail_client import MAILBOX


def thread(mid='m1', outbound=False):
    return {'id': 't1', 'messages': [{'id': mid, 'from': MAILBOX if outbound else 'Owner <seller@example.com>',
        'to': MAILBOX, 'subject': 'Property question', 'message-id': f'<{mid}@example.com>',
        'timestamp': '2026-09-29T12:00:00+00:00', 'body': 'Can we discuss the property?',
        'labels': ['SENT'] if outbound else ['INBOX'], 'attachments': []}]}


def suggestion(_):
    return dict(summary='Owner asked to discuss a property.', reply_reason='Direct question.',
                draft_text='Yes, what works for you?', review_state='needs_review')


class FakeGmail:
    def __init__(self):
        self.thread = thread()
        self.sent = []
        self.found = None
        self.error = None
    def verify_identity(self):
        return MAILBOX
    def get_thread(self, tid):
        return copy.deepcopy(self.thread)
    def list_threads(self, query):
        self.query = query
        return ['t1']
    def send_reply(self, data, recipient, text, rfc_id):
        self.sent.append((recipient, text, rfc_id))
        if self.error:
            raise self.error
        return {'id': 'sent123'}
    def find_sent(self, rfc_id):
        return self.found


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / 'crm.db'
    db.migrate_email(path)
    review.store_thread(thread(), path, suggestion)
    return path, FakeGmail()


def test_edited_text_sent_once_and_accepted_is_recorded(setup):
    path, client = setup
    assert review.approve(client, 't1', 'm1', 'My exact edited reply', 'RJ', path) == 'sent123'
    assert client.sent[0][1] == 'My exact edited reply'
    assert db.email_review(MAILBOX, 't1', path)['review_state'] == 'answered'
    with pytest.raises(review.ReviewChanged):
        review.approve(client, 't1', 'm1', 'Duplicate', 'RJ', path)
    assert len(client.sent) == 1


def test_denial_persists_until_new_message(setup):
    path, client = setup
    review.save_edit('t1', 'm1', 'Saved', 'dismissed', path)
    review.sync(client, path, suggestion)
    assert db.email_review(MAILBOX, 't1', path)['review_state'] == 'dismissed'
    client.thread = thread('m2')
    review.sync(client, path, suggestion)
    assert db.email_review(MAILBOX, 't1', path)['review_state'] == 'needs_review'
    assert client.sent == []


@pytest.mark.parametrize('outbound', [True, False])
def test_changed_conversation_never_sends_old_reply(setup, outbound, monkeypatch):
    path, client = setup
    monkeypatch.setattr(review, 'store_thread', lambda *a, **k: None)
    client.thread = thread('m2', outbound)
    with pytest.raises(review.ReviewChanged):
        review.approve(client, 't1', 'm1', 'Old reply', 'RJ', path)
    assert client.sent == []


def test_gmail_outbound_removes_need_for_reply(setup):
    path, client = setup
    client.thread = thread('m2', True)
    review.sync(client, path, suggestion)
    assert db.email_review(MAILBOX, 't1', path)['review_state'] == 'answered'
    assert client.sent == []


def test_unknown_send_blocks_retry_and_reconciles(setup):
    path, client = setup
    client.error = TimeoutError()
    with pytest.raises(RuntimeError, match='unknown'):
        review.approve(client, 't1', 'm1', 'Reply', 'RJ', path)
    assert db.email_attempts(MAILBOX, path)[0]['send_state'] == 'unknown'
    with pytest.raises(review.ReviewChanged, match='already exists'):
        review.approve(client, 't1', 'm1', 'Reply again', 'RJ', path)
    assert len(client.sent) == 1
    client.found = {'id': 'reconciled'}
    review.sync(client, path, suggestion)
    assert db.email_attempts(MAILBOX, path)[0]['gmail_message_id'] == 'reconciled'
    assert db.email_review(MAILBOX, 't1', path)['review_state'] == 'answered'


def test_definite_api_rejection_is_not_success(setup):
    path, client = setup
    client.error = RuntimeError('API rejected')
    client.error.resp = SimpleNamespace(status=403)
    with pytest.raises(RuntimeError, match='rejected'):
        review.approve(client, 't1', 'm1', 'Reply', 'RJ', path)
    assert db.email_attempts(MAILBOX, path)[0]['send_state'] == 'failed'
    assert db.email_review(MAILBOX, 't1', path)['review_state'] == 'needs_review'


def test_blank_reply_and_stale_edits_rejected(setup):
    path, client = setup
    with pytest.raises(ValueError):
        review.approve(client, 't1', 'm1', '   ', 'RJ', path)
    with pytest.raises(review.ReviewChanged):
        review.save_edit('t1', 'old', 'Edit', db_path=path)
    assert client.sent == []


def test_ai_failure_keeps_email_visible(monkeypatch):
    import anthropic
    monkeypatch.setattr(anthropic, 'Anthropic', lambda **k: (_ for _ in ()).throw(RuntimeError('offline')))
    result = review.suggest(thread())
    assert result['review_state'] == 'needs_review'
    assert result['draft_text'] == ''


def test_failed_fetch_preserves_last_success(setup):
    path, client = setup
    db.save_email_sync(MAILBOX, '2026-09-01T00:00:00+00:00', None, db_path=path)
    client.get_thread = lambda tid: (_ for _ in ()).throw(RuntimeError('offline'))
    with pytest.raises(RuntimeError, match='could not be refreshed'):
        review.sync(client, path, suggestion)
    assert db.email_sync_status(MAILBOX, path)['last_success_at'] == '2026-09-01T00:00:00+00:00'
    assert 'after:' in client.query


def test_saved_edit_survives_sync(setup):
    path, client = setup
    review.save_edit('t1', 'm1', 'Keep my edit', db_path=path)
    review.sync(client, path, suggestion)
    assert db.email_review(MAILBOX, 't1', path)['draft_text'] == 'Keep my edit'


def test_lock_blocks_second_process_handle(tmp_path):
    with review.email_lock(tmp_path):
        with pytest.raises(RuntimeError, match='already running'):
            with review.email_lock(tmp_path):
                pass


def test_review_ui_saves_edits_and_requires_explicit_approval(setup, tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from crm_engine import email_ui
    from types import SimpleNamespace
    path, client = setup
    (tmp_path / 'gmail-token.json').write_text('{}')
    monkeypatch.setattr(email_ui, 'LOCAL', tmp_path)
    monkeypatch.setattr(email_ui, 'db', SimpleNamespace(
        email_ready=lambda: True,
        email_sync_status=lambda mailbox: {},
        email_reviews=lambda mailbox: db.email_reviews(mailbox, path),
        email_attempts=lambda mailbox: db.email_attempts(mailbox, path)))
    monkeypatch.setattr(email_ui, 'email_lock', lambda: review.email_lock(tmp_path))
    monkeypatch.setattr(email_ui, 'save_edit', lambda tid, basis, text, state='needs_review':
                        review.save_edit(tid, basis, text, state, path))
    monkeypatch.setattr(email_ui, 'approve', lambda cli, tid, basis, text, actor:
                        review.approve(cli, tid, basis, text, actor, path))
    monkeypatch.setattr(email_ui.GmailClient, 'connect', lambda: client)
    app = AppTest.from_string('from crm_engine.email_ui import render_email_review\nrender_email_review()').run()
    assert not app.exception
    assert client.sent == []
    app.text_area[0].set_value('This is my edited reply').run()
    next(b for b in app.button if b.label == 'Save edit').click().run()
    assert not app.exception
    assert client.sent == []
    assert db.email_review(MAILBOX, 't1', path)['draft_text'] == 'This is my edited reply'
    next(b for b in app.button if b.label == 'Approve & send').click().run()
    assert not app.exception
    assert client.sent[0][1] == 'This is my edited reply'
    assert any('Gmail accepted' in item.value for item in app.success)


def test_review_ui_disconnected_shows_setup(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from crm_engine import email_ui
    monkeypatch.setattr(email_ui, 'LOCAL', tmp_path)
    monkeypatch.setattr(email_ui, 'db', SimpleNamespace(email_ready=lambda: True,
                                                      email_sync_status=lambda mailbox: {}))
    app = AppTest.from_string('from crm_engine.email_ui import render_email_review\nrender_email_review()').run()
    assert not app.exception
    assert any('Connect Gmail once' in item.value for item in app.markdown)
    assert not any(b.label == 'Approve & send' for b in app.button)
