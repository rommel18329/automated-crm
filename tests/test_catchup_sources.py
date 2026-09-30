import json
from pathlib import Path

from crm_engine.catchup_sources import ContextIndex, read_voice_export, notification_advice, direct_text


def mail(tid='t1', mid='m1', sender='seller@example.com', stamp='2026-09-20T12:00:00+00:00', body='Still open to selling.', outgoing=False):
    from crm_engine.gmail_client import MAILBOX
    return {'id': tid, 'messages': [{'id': mid, 'from': MAILBOX if outgoing else sender,
        'to': sender if outgoing else MAILBOX, 'body': body, 'subject': 'Property',
        'timestamp': stamp, 'labels': ['SENT'] if outgoing else ['INBOX'], 'message-id': '<m@example.com>'}]}


def local(leads=None):
    return {'leads': leads or [], 'interactions': [], 'unmatched_inbox': []}


def test_email_match_is_exact_and_names_alone_are_not_matches():
    t = mail(sender='Jane <seller@example.com>')
    data = local([{'id': 1, 'name': 'Jane', 'phone': '5155551234', 'timeline': 'Email: other@example.com.', 'status': 'warm'}])
    assert ContextIndex(data, [], [], [t]).attach(t)['cross_channel']['lead_id'] is None
    data['leads'][0]['timeline'] = 'Email: seller@example.com.'
    assert ContextIndex(data, [], [], [t]).attach(t)['cross_channel']['lead_id'] == 1


def test_google_voice_threads_group_and_old_notifications_are_suppressed():
    alias = '17732178686.15155551234.hash@txt.voice.google.com'
    old, new = mail(sender=alias), mail('t2', 'm2', alias, '2026-09-21T12:00:00+00:00', outgoing=True)
    data = local([{'id': 1, 'name': 'Seller', 'phone': '5155551234', 'timeline': '', 'status': 'warm'}])
    index = ContextIndex(data, [], [], [old, new])
    old_context = index.attach(old)['cross_channel']
    assert old_context['lead_id'] == 1
    assert old_context['kind'] == 'voice_text'
    assert old_context['superseded']
    assert len(old_context['events']) == 2


def test_duplicate_phone_contacts_are_not_silently_merged():
    alias = '17732178686.15155551234.hash@txt.voice.google.com'
    t = mail(sender=alias)
    data = local([{'id': n, 'name': f'Contact {n}', 'phone': '5155551234', 'timeline': ''} for n in (1, 2)])
    context = ContextIndex(data, [], [], [t]).attach(t)['cross_channel']
    assert context['ambiguous'] and context['blocked']
    assert context['lead_id'] is None


def test_unknown_twilio_coverage_blocks_send_and_hash_ignores_check_time():
    t = mail()
    source = {'source': 'Twilio', 'complete': False, 'detail': 'Account inactive', 'checked_at': 'now'}
    first = ContextIndex(local(), [], [source], [t]).attach(t)['cross_channel']
    source['checked_at'] = 'later'
    second = ContextIndex(local(), [], [source], [t]).attach(t)['cross_channel']
    assert first['blocked']
    assert first['fingerprint'] == second['fingerprint']


def test_takeout_parse_preserves_sender_direction_without_import(tmp_path):
    p = tmp_path / '+15155551234 - Text - 2026-01-01.html'
    p.write_text('''<div class="message"><abbr class="dt" title="2026-01-01T10:00:00-06:00"></abbr>
    <cite class="sender"><a href="tel:+17732178686">Me</a></cite><q>Our question</q></div>
    <div class="message"><abbr class="dt" title="2026-01-01T11:00:00-06:00"></abbr>
    <cite class="sender"><a href="tel:+15155551234">Seller</a></cite><q>Their answer</q></div>''')
    events, status = read_voice_export(tmp_path)
    assert [e['direction'] for e in events] == ['outbound', 'inbound']
    assert events[0]['timestamp'] == '2026-01-01T16:00:00+00:00'
    assert status['complete']
    assert list(tmp_path.iterdir()) == [p]


def test_notifications_are_not_draft_recipients_but_sms_is_human():
    assert notification_advice(mail(sender='no-reply@accounts.google.com'))['draft_text'] == ''
    assert notification_advice(mail(sender='17732178686.15155551234.hash@txt.voice.google.com')) is None


def test_quote_and_voice_footer_removed_without_changing_new_statement():
    assert direct_text('Our answer\nOn Monday seller wrote:\n> old question') == 'Our answer'
    assert direct_text('<https://voice.google.com>\nYes\nTo respond to this text message, reply to email') == 'Yes'
