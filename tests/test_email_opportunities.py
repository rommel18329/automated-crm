from datetime import date
import json

from crm_engine import email_opportunities as opportunities, email_review, db
from crm_engine.catchup_sources import ContextIndex
from crm_engine.gmail_client import MAILBOX
from test_catchup_sources import mail, local


def answer(**kwargs):
    return {'is_opportunity': True, 'category': 'seller', 'priority': 4, 'summary': 'Seller was open to discussing terms.',
        'reason': 'Specific property discussion is unfinished.', 'next_step': 'Ask if plans changed.',
        'stage': 'reopen', 'draft_text': 'Are you still open to discussing the property?',
        'confidence': 'medium', 'evidence_ids': ['m1'], **kwargs}


def test_calendar_windows_include_six_month_and_year_boundaries():
    today = date(2026, 9, 29)
    assert opportunities.period('2026-03-29T00:00:00', today) == 'Last 6 months'
    assert opportunities.period('2026-03-28T23:59:00', today) == '6–12 months ago'
    assert opportunities.period('2025-09-29T00:00:00', today) == '6–12 months ago'
    assert opportunities.period('2025-09-28T23:59:00', today) == 'Outside last year'


def test_routine_notifications_skipped_without_ai_call():
    def never(*args):
        raise AssertionError('AI should not be called')
    result = opportunities.analyze(mail(sender='no-reply@accounts.google.com'), ai=never)
    assert not result['is_opportunity']
    assert result['analysis_state'] == 'complete'


def test_real_stalled_conversation_can_be_reopened_even_after_our_outbound():
    t = mail()
    t['messages'].append(mail('t1', 'm2', outgoing=True)['messages'][0])
    result = opportunities.analyze(t, ai=lambda system, context: answer())
    assert result['is_opportunity'] and result['draft_text']


def test_waiting_on_them_never_produces_duplicate_draft():
    result = opportunities.analyze(mail(), ai=lambda *args: answer(stage='wait'))
    assert result['is_opportunity'] and not result['draft_text']


def test_unsupported_evidence_cannot_become_opportunity():
    result = opportunities.analyze(mail(), ai=lambda *args: answer(evidence_ids=['invented']))
    assert result['analysis_state'] == 'failed'
    assert not result['is_opportunity']


def test_superseded_and_optout_contacts_are_excluded():
    for flag in ('superseded', 'optout', 'terminal'):
        t = mail()
        t['cross_channel'] = {flag: True}
        assert not opportunities.analyze(t, ai=lambda *args: answer())['is_opportunity']


def test_all_context_events_reach_analysis_including_failed_texts():
    t = mail()
    events = [{'id': str(n), 'channel': 'text', 'direction': 'inbound', 'body': 'Question '+str(n),
               'status': 'historical', 'timestamp': '2026-09-20T12:00:00+00:00'} for n in range(60)]
    events.append({'id': 'failed', 'channel': 'text', 'direction': 'outbound', 'body': 'Never delivered',
                   'status': 'failed', 'timestamp': '2026-09-21T12:00:00+00:00'})
    t['cross_channel'] = {'events': events}
    def analyze(system, payload):
        assert len(payload['conversation']) == 61
        assert payload['conversation'][-1]['status'] == 'failed'
        return answer(evidence_ids=['0'])
    assert opportunities.analyze(t, ai=analyze)['is_opportunity']


def test_cross_context_changed_blocks_email_before_transport(tmp_path, monkeypatch):
    path = tmp_path / 'crm.db'
    db.migrate_email(path)
    t = mail()
    data = local([{'id': 1, 'name': 'Seller', 'phone': '5155551234', 'timeline': 'Email: seller@example.com.'}])
    source = [{'source': 'Twilio', 'complete': True, 'detail': 'Read', 'checked_at': 'now'}]
    old_index = ContextIndex(data, [], source, [t])
    old = old_index.attach(t)
    monkeypatch.setattr(email_review, 'analyze', lambda thread: {**answer(stage='needs_reply'), 'analysis_state': 'complete', 'version': 1})
    email_review.store_thread(old, path)
    class Client:
        def verify_identity(self): return MAILBOX
        def get_thread(self, tid): return t
        def list_threads(self, query): return ['t1']
        def send_reply(self, *args): raise AssertionError('Transport must never run')
    extra = {'id': 'new-text', 'source': 'Twilio', 'channel': 'text', 'phone': '+15155551234',
             'timestamp': '2026-09-22T10:00:00+00:00', 'direction': 'inbound', 'body': 'Already sold', 'status': 'received'}
    builder = lambda threads, db_path: ContextIndex(data, [extra], source, threads)
    import pytest
    with pytest.raises(email_review.ReviewChanged, match='context changed'):
        email_review.approve(Client(), 't1', 'm1', 'Old draft', 'RJ', path,
            context_builder=builder, context_basis=old['cross_channel']['fingerprint'])
    assert db.email_attempts(MAILBOX, path) == []


def test_large_history_is_fully_examined_in_one_priced_call():
    # One call per conversation, so the priced prompt is exactly the prompt that runs.
    t = mail()
    events = [{'id': str(n), 'channel': 'text', 'direction': 'inbound', 'body': str(n) + 'x' * 1200,
               'status': 'historical', 'timestamp': '2026-09-20T12:00:00+00:00'} for n in range(100)]
    t['cross_channel'] = {'events': events}
    calls = []
    def fake(system, payload):
        calls.append(json.dumps(payload, ensure_ascii=False))
        return answer(evidence_ids=['0'])
    result = opportunities.analyze(t, ai=fake)
    assert result['is_opportunity']
    assert len(calls) == 1
    assert {e['id'] for e in json.loads(calls[0])['conversation']} == {str(n) for n in range(100)}
    assert opportunities.prepare_analysis_request(t)['messages'][0]['content'] == calls[0]


def test_snapshot_analysis_persists_results_and_reports_incomplete(tmp_path, monkeypatch):
    from crm_engine import opportunity_scan
    path = tmp_path / 'crm.db'
    db.migrate_email(path)
    monkeypatch.setattr(opportunity_scan, 'LOCAL', tmp_path)
    ts = [mail(), mail('t2', 'm2', 'second@example.com')]
    index = ContextIndex(local(), [], [], ts)
    def fake(t):
        if t['id'] == 't1':
            return {**answer(stage='needs_reply'), 'analysis_state': 'complete', 'version': 1}
        return {'is_opportunity': False, 'analysis_state': 'failed', 'summary': 'Retry', 'reason': 'Retry', 'draft_text': ''}
    result = opportunity_scan.analyze_snapshot(ts, path, index=index, analyzer=fake)
    assert result['state'] == 'analysis needs retry'
    assert result['processed'] == 2
    assert db.email_opportunity_count(MAILBOX, path) == 1
    assert len(db.email_reviews(MAILBOX, path)) == 2
    assert db.email_attempts(MAILBOX, path) == []


def test_unsupported_financing_terms_never_reach_review():
    result = opportunities.analyze(mail(), ai=lambda *args: answer(
        draft_text="I'm thinking $640k, 20% down at signing, financed at 7.5%."))
    assert result['analysis_state'] == 'failed'
    assert result['draft_text'] == ''


def test_prior_staff_personal_details_are_not_borrowed():
    result = opportunities.analyze(mail(body='My husband and I visited before.'),
        ai=lambda *args: answer(draft_text='My husband and I can come tomorrow.'))
    assert result['analysis_state'] == 'failed'
    assert result['draft_text'] == ''


def test_bad_offer_draft_is_rejected_without_a_second_paid_review():
    # The second "skeptical reviewer" call was removed so each analysis has one
    # known price; the deterministic draft checks still block unsupported offers.
    calls = []
    def ai(system, payload):
        calls.append(payload)
        return answer(draft_text='I can pay $500000.')
    result = opportunities.analyze(mail(), ai=ai)
    assert len(calls) == 1
    assert result['analysis_state'] == 'failed'
    assert result['draft_text'] == ''


def test_unapproved_analysis_never_overwrites_the_last_good_one(tmp_path, monkeypatch):
    path = tmp_path / 'crm.db'
    db.migrate_email(path)
    t = mail()
    index = ContextIndex(local(), [], [], [t])
    good = {**answer(stage='needs_reply'), 'analysis_state': 'complete', 'version': opportunities.VERSION,
            'evaluated_on': '2026-01-01', 'draft_text': 'Are you still open to discussing the property?'}
    email_review.store_thread(index.attach(t), path, opportunity_result=good)
    # A later sync with no price approval: analysis is attempted but cannot run.
    email_review.store_thread(index.attach(mail()), path)
    row = db.email_review(MAILBOX, 't1', path)
    kept = json.loads(row['thread_json'])['opportunity']
    assert kept['analysis_state'] == 'complete'
    assert kept['draft_text'] == good['draft_text']
    assert row['draft_text'] == good['draft_text']
    # A brand-new conversation is parked as waiting for approval, not marked failed.
    other = mail('t2', 'm2', 'second@example.com')
    email_review.store_thread(ContextIndex(local(), [], [], [other]).attach(other), path)
    parked = json.loads(db.email_review(MAILBOX, 't2', path)['thread_json'])['opportunity']
    assert parked['analysis_state'] == 'pending_price'
    assert db.email_review(MAILBOX, 't2', path)['review_state'] == 'needs_review'
