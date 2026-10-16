import json
from types import SimpleNamespace

from crm_engine import sms_context as context
from crm_engine import ai_message_generator as ai


LEAD = {'id': 1, 'name': 'Pat Example', 'phone': '+13125550123', 'timeline': 'Email: pat@example.com'}


def thread(tid='t1', sender='pat@example.com', body='Tuesday works.', outgoing=False):
    return {'id': tid, 'messages': [{'id': tid + '-m', 'timestamp': '2026-09-30T12:00:00+00:00',
        'from': context.MAILBOX if outgoing else sender,
        'to': sender if outgoing else context.MAILBOX, 'subject': 'Property visit',
        'body': body, 'labels': ['SENT'] if outgoing else ['INBOX']}]}


class FakeGmail:
    def __init__(self, threads):
        self.threads = threads
        self.queries = []

    def list_threads(self, query):
        self.queries.append(query)
        return iter(t['id'] for t in self.threads)

    def get_threads(self, ids):
        return (t for t in self.threads if t['id'] in ids)


def setup(monkeypatch, tmp_path, leads=None, cached=None):
    from contextlib import nullcontext
    from crm_engine import email_review
    monkeypatch.setattr(email_review, 'email_lock', nullcontext)
    monkeypatch.setattr(context.db, 'email_context_data', lambda path: {
        'leads': leads or [LEAD], 'interactions': [], 'unmatched_inbox': []})
    monkeypatch.setattr(context.db, 'email_reviews', lambda mailbox, path: [])
    snapshot = tmp_path / 'snapshot.jsonl'
    snapshot.write_text('\n'.join(json.dumps(t) for t in (cached or [])), encoding='utf-8')
    return snapshot


def test_refreshes_sent_and_received_email_but_not_same_name(monkeypatch, tmp_path):
    snapshot = setup(monkeypatch, tmp_path)
    client = FakeGmail([thread(), thread('t2', body='Can you do Tuesday?', outgoing=True),
                        thread('t3', sender='Pat Example <other@example.com>')])
    result = context.load_email_context(LEAD, connector=lambda: client, snapshot=snapshot)
    assert result['complete']
    assert len(result['messages']) == 2
    assert {m['direction'] for m in result['messages']} == {'inbound', 'outbound'}
    assert 'pat@example.com' in client.queries[0]
    assert 'Pat Example' not in client.queries[0]


def test_ambiguous_shared_email_does_not_merge(monkeypatch, tmp_path):
    other = {**LEAD, 'id': 2, 'phone': '+13125550999'}
    snapshot = setup(monkeypatch, tmp_path, leads=[LEAD, other])
    result = context.load_email_context(LEAD, connector=lambda: FakeGmail([thread()]), snapshot=snapshot)
    assert result['messages'] == []
    assert 'no confidently matched' in result['status']


def test_phone_signature_discovers_address_and_searches_other_threads(monkeypatch, tmp_path):
    lead = {**LEAD, 'timeline': ''}
    snapshot = setup(monkeypatch, tmp_path, leads=[lead])
    client = FakeGmail([thread('t1', body='Phone: 312-555-0123\nTuesday works.'), thread('t2')])
    result = context.load_email_context(lead, connector=lambda: client, snapshot=snapshot)
    assert len(client.queries) == 2
    assert 'from:pat@example.com' in client.queries[1]
    assert len(result['messages']) == 2


def test_refresh_failure_labels_cache_and_does_not_claim_current(monkeypatch, tmp_path):
    snapshot = setup(monkeypatch, tmp_path, cached=[thread()])
    def offline():
        raise RuntimeError('offline')
    result = context.load_email_context(LEAD, connector=offline, snapshot=snapshot)
    assert not result['complete']
    assert len(result['messages']) == 1
    assert 'cached' in result['status']


def test_google_voice_relay_is_not_mislabeled_as_email(monkeypatch, tmp_path):
    snapshot = setup(monkeypatch, tmp_path)
    client = FakeGmail([thread(sender='17732178686.13125550123.abc@txt.voice.google.com')])
    assert context.load_email_context(LEAD, connector=lambda: client, snapshot=snapshot)['messages'] == []


def test_live_thread_replaces_cached_body(monkeypatch, tmp_path):
    snapshot = setup(monkeypatch, tmp_path, cached=[thread(body='Old proposal')])
    result = context.load_email_context(LEAD, connector=lambda: FakeGmail([thread(body='Already agreed by email')]), snapshot=snapshot)
    assert result['complete']
    assert [m['content'] for m in result['messages']] == ['Already agreed by email']


def test_limits_are_disclosed_instead_of_claiming_full_coverage(monkeypatch, tmp_path):
    snapshot = setup(monkeypatch, tmp_path)
    client = FakeGmail([thread(str(i), body='x' * 2100) for i in range(45)])
    result = context.load_email_context(LEAD, connector=lambda: client, snapshot=snapshot)
    assert not result['complete']
    assert len(result['messages']) == 20
    assert all(len(m['content']) == 2000 for m in result['messages'])
    assert '40 newest' in result['status']
    assert 'latest 20' in result['status']


def test_unavailable_identifiers_do_not_search_by_name(monkeypatch, tmp_path):
    lead = {**LEAD, 'phone': '', 'timeline': ''}
    snapshot = setup(monkeypatch, tmp_path, leads=[lead])
    result = context.load_email_context(lead, connector=lambda: (_ for _ in ()).throw(AssertionError()), snapshot=snapshot)
    assert not result['complete']
    assert 'no confirmed email' in result['status']


def test_generator_includes_email_and_preserves_recommendation_to_wait(monkeypatch):
    email = {'type': 'email', 'id': 'm1', 'timestamp': '2026-09-30T12:00:00+00:00',
             'direction': 'outbound', 'subject': 'Offer', 'content': 'Would Tuesday work for a visit?'}
    monkeypatch.setattr(ai, 'load_email_context', lambda lead: {
        'messages': [email], 'status': 'Gmail checked: 1 email.', 'complete': True})
    captured = {}
    def run_request(approval, request):
        captured.update(request)
        return SimpleNamespace(content=[SimpleNamespace(type='text', text=json.dumps({
            'message': '', 'coaching': 'You already emailed this question. Wait for their reply.'}))],
            usage=SimpleNamespace(input_tokens=10, output_tokens=10))
    monkeypatch.setattr(ai, 'run_request', run_request)
    draft = ai.generate_message(LEAD, [], 'Follow up', 'Casual', 'Ask about Tuesday', approval='approved')
    prompt = captured['messages'][0]['content']
    assert 'EMAIL YOU' in prompt
    assert email['content'] in prompt
    assert 'return an empty message' in captured['system']
    assert draft.message == ''
    assert 'Gmail checked' in draft.coaching
    assert 'already emailed' in draft.coaching
