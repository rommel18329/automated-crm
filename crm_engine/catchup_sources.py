"""Read-only cross-channel context for email review. This module cannot send SMS."""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from crm_engine import db
from crm_engine.gmail_client import ROOT, MAILBOX, addresses

VOICE_EXPORT = Path(r'C:\Users\jimen\Desktop\Takeout\Voice\Calls')
EMAIL_RE = re.compile(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}')
PHONE_RE = re.compile(r'(?<!\d)(?:\+?1[ .-]?)?\(?[2-9]\d{2}\)?[ .-]?\d{3}[ .-]?\d{4}(?!\d)')


def stamp():
    return datetime.now(timezone.utc).isoformat()


def phone(value):
    digits = re.sub(r'\D', '', value or '')
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    return '+1' + digits if len(digits) == 10 else None


def utc(value):
    if not value:
        return ''
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return ''


def own_phones():
    return {p for p in [phone(os.getenv(k, '')) for k in
            ('TWILIO_FROM_NUMBER', 'TWILIO_FROM_IL_NUMBER', 'MY_CELL_NUMBER')] +
            ['+17732178686', '+15154618235'] if p}


def direct_text(text):
    """Strip quoted email and Google Voice footer before interpreting a new statement."""
    text = re.split(r'(?im)^\s*(?:On .+wrote:|>\s*|To respond to this text message|YOUR ACCOUNT)', text or '')[0]
    return text.replace('<https://voice.google.com>', '').strip()


def relay_phone(address):
    match = re.fullmatch(r'\d{11}\.(\d{11})\.[^@]+@txt\.voice\.google\.com', address.lower())
    return phone(match[1]) if match else None


def lead_phones(lead):
    result = {phone(lead.get('phone'))}
    alt = re.search(r'Alt phones:\s*([^\n]+)', lead.get('timeline') or '', re.I)
    if alt:
        result.update(phone(m[0]) for m in PHONE_RE.finditer(alt[1]))
    return result - {None}


def read_twilio():
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    account, token = os.getenv('TWILIO_ACCOUNT_SID'), os.getenv('TWILIO_AUTH_TOKEN')
    status = {'source': 'Twilio', 'checked_at': stamp(), 'complete': False}
    if not account or not token:
        return [], {**status, 'detail': 'Twilio credentials are not configured.'}
    try:
        from twilio.rest import Client
        from twilio.http.http_client import TwilioHttpClient
        client = Client(account, token, http_client=TwilioHttpClient(timeout=35))
        events = []
        for message in client.messages.stream(page_size=1000):
            inbound = message.direction == 'inbound'
            remote = phone(message.from_ if inbound else message.to)
            if not remote:
                continue
            events.append({'id': message.sid, 'source': 'Twilio', 'channel': 'text',
                'phone': remote, 'timestamp': (message.date_sent or message.date_created).astimezone(timezone.utc).isoformat(),
                'direction': 'inbound' if inbound else 'outbound', 'body': message.body or '',
                'status': message.status})
        return events, {**status, 'complete': True, 'count': len(events),
                        'detail': 'Read all message records currently available from this Twilio account.'}
    except Exception:
        # Do not leak account IDs, tokens or provider response bodies into the UI.
        return [], {**status, 'detail': 'Twilio history unavailable. Check account activation, balance and credentials. Current text history is unverified.'}


def read_voice_export(directory=VOICE_EXPORT):
    """Read Takeout only; do not invoke the legacy importer or write CRM interactions."""
    from bs4 import BeautifulSoup
    status = {'source': 'Google Voice export', 'checked_at': stamp(), 'complete': False}
    if not directory.exists():
        return [], {**status, 'detail': 'Original Takeout export not available; imported CRM history still included.'}
    events, failed, files = [], 0, 0
    for path in sorted(directory.glob('*.html')):
        kind = re.search(r' - (Text|Placed|Received|Missed|Voicemail) - ', path.name)
        if not kind:
            continue
        files += 1
        try:
            soup = BeautifulSoup(path.read_text(encoding='utf-8', errors='replace'), 'html.parser')
            remotes = {phone(a.get('href', '')[4:]) for a in soup.select('a[href^="tel:"]')} - own_phones() - {None}
            filename_phone = re.match(r'^(\+1\d{10}) - ', path.name)
            if filename_phone:
                remotes.add(filename_phone[1])
            if len(remotes) != 1:
                failed += 1
                continue
            remote = next(iter(remotes))
            if kind[1] == 'Text':
                for n, element in enumerate(soup.select('div.message')):
                    when = element.select_one('abbr.dt')
                    sender = element.select_one('cite.sender')
                    body = element.find('q')
                    sender_link = sender.select_one('a[href^="tel:"]') if sender else None
                    sender_phone = phone(sender_link['href'][4:]) if sender_link else None
                    is_me = bool(sender and sender.get_text(' ', strip=True) == 'Me') or sender_phone in own_phones()
                    if not when or not body or (not is_me and sender_phone != remote):
                        failed += 1
                        continue
                    ts = utc(when.get('title'))
                    if ts:
                        events.append({'id': f'{path.name}:{n}', 'source': 'Google Voice export', 'channel': 'text',
                            'phone': remote, 'timestamp': ts, 'direction': 'outbound' if is_me else 'inbound',
                            'body': body.get_text(' ', strip=True), 'status': 'historical'})
            else:
                when = soup.select_one('abbr.published')
                ts = utc(when.get('title')) if when else ''
                if ts:
                    transcript = soup.find('q')
                    events.append({'id': path.name, 'source': 'Google Voice export', 'channel': 'call',
                        'phone': remote, 'timestamp': ts, 'direction': 'outbound' if kind[1] == 'Placed' else 'inbound',
                        'body': kind[1] + (' · ' + transcript.get_text(' ', strip=True) if transcript else ''),
                        'status': 'historical'})
        except Exception:
            failed += 1
    latest = max((e['timestamp'] for e in events), default='unknown')
    return events, {**status, 'complete': failed == 0, 'count': len(events), 'files': files,
                    'detail': f'Historical export only; latest record {latest}. Unresolved files/messages: {failed}. Not a live Google Voice sync.'}


class ContextIndex:
    def __init__(self, local, events, statuses, threads):
        self.leads = {r['id']: r for r in local['leads']}
        self.statuses = statuses
        self.phone_leads, self.email_leads = {}, {}
        for lead in self.leads.values():
            for value in lead_phones(lead):
                self.phone_leads.setdefault(value, set()).add(lead['id'])
            # Only explicitly recorded contact-email fields, not arbitrary quoted addresses.
            for value in re.findall(r'(?i)Email:\s*([\w.+-]+@[\w.-]+\.[A-Za-z]{2,})', lead.get('timeline') or ''):
                self.email_leads.setdefault(value.lower(), set()).add(lead['id'])
        self.events = list(events)
        for row in local['interactions']:
            lead = self.leads.get(row['lead_id'])
            if not lead:
                continue
            self.events.append({'id': 'crm:' + str(row['id']), 'source': 'CRM (including imported history)',
                'channel': row['type'], 'lead_id': row['lead_id'], 'phone': phone(lead['phone']),
                'timestamp': utc(row['timestamp']), 'direction': row['direction'], 'body': row['content'],
                'status': 'failed' if row['content'].startswith(('[SEND FAILED]', '[CALL FAILED]')) else 'historical'})
        for row in local['unmatched_inbox']:
            self.events.append({'id': 'unmatched:' + str(row['id']), 'source': 'CRM unmatched inbox',
                'channel': 'text' if row['type'] == 'text' else 'call', 'phone': phone(row['phone']),
                'timestamp': utc(row['timestamp']), 'direction': 'inbound', 'body': row['content'], 'status': 'historical'})
        self.threads = threads
        self.identities = {t['id']: self.identity(t) for t in threads if t.get('messages')}

    def identity(self, thread):
        incoming = [m for m in thread['messages'] if 'SENT' not in m.get('labels', [])
                    and MAILBOX not in addresses(m.get('from'))]
        senders = {a for m in incoming for a in addresses(m.get('from'))}
        if not senders:
            senders = {a for m in thread['messages'] for a in addresses(m.get('to')) if a != MAILBOX}
        remotes = {relay_phone(a) for a in senders} - {None}
        kind = 'voice_text' if remotes else 'email'
        if 'voice-noreply@google.com' in senders:
            kind = 'voice_call'
            for m in incoming:
                remotes.update(phone(p[0]) for p in PHONE_RE.finditer(m.get('subject', '')))
        if kind == 'email':
            # Only labeled telephone numbers in unquoted sender text are evidence.
            for m in incoming:
                for match in re.finditer(r'(?im)^(?:phone|mobile|cell|tel)[ :]+([^\n]+)', direct_text(m.get('body', ''))):
                    remotes.update(phone(p[0]) for p in PHONE_RE.finditer(match[1]))
        remotes -= own_phones() | {None}
        candidates = set()
        for remote in remotes:
            candidates.update(self.phone_leads.get(remote, set()))
        for sender in senders:
            candidates.update(self.email_leads.get(sender, set()))
        ambiguous = len(candidates) > 1 or len(remotes) > 1
        lead_id = next(iter(candidates)) if len(candidates) == 1 and not ambiguous else None
        if lead_id:
            remotes.update(lead_phones(self.leads[lead_id]))
        contact = ('lead:' + str(lead_id) if lead_id else 'phone:' + next(iter(remotes))
                   if len(remotes) == 1 else 'email:' + '|'.join(sorted(senders)))
        return {'contact_key': contact, 'kind': kind, 'lead_id': lead_id, 'phones': sorted(remotes),
                'ambiguous': ambiguous, 'sender_emails': sorted(senders)}

    def attach(self, thread):
        identity = self.identity(thread)
        related = [t for t in self.threads if self.identities.get(t['id'], {}).get('contact_key') == identity['contact_key']]
        events = [e for e in self.events if not identity['ambiguous'] and
                  ((identity['lead_id'] is not None and e.get('lead_id') == identity['lead_id']) or
                   e.get('phone') in identity['phones'])]
        for other in related:
            for m in other['messages']:
                outgoing = MAILBOX in addresses(m.get('from')) or 'SENT' in m.get('labels', [])
                events.append({'id': m['id'], 'source': 'Gmail / Google Voice notifications',
                    'thread_id': other['id'], 'channel': 'text' if self.identities.get(other['id'], {}).get('kind') == 'voice_text' else 'email',
                    'timestamp': m['timestamp'], 'direction': 'outbound' if outgoing else 'inbound',
                    'body': direct_text(m.get('body', '')), 'status': 'recorded'})
        # Provider-ID dedup preserves delivery status; content dedup avoids duplicate imported events.
        unique = {}
        for e in events:
            key = (e['channel'], e['timestamp'][:19], e['direction'], re.sub(r'\s+', ' ', e['body']).strip())
            if key not in unique or e['source'] == 'Twilio':
                unique[key] = e
        events = sorted(unique.values(), key=lambda e: (e['timestamp'], e['id']))
        substantive = [t for t in related if not notification_advice(t)] or related
        representative = max(substantive, key=lambda t: (t['messages'][-1]['timestamp'], t['id']), default=thread)
        # SMS notifications are fragments of a single phone conversation; keep only its newest card actionable.
        superseded = representative['id'] != thread['id']
        lead = self.leads.get(identity['lead_id'])
        alerts = []
        if identity['ambiguous']:
            alerts.append('Multiple possible contacts match. Identity must be confirmed; their histories were not combined.')
        if not identity['phones']:
            alerts.append('No confirmed phone link for this sender. Text overlap cannot be ruled out.')
        for source in self.statuses:
            if not source['complete']:
                alerts.append(source['detail'])
        optout = any(e['direction'] == 'inbound' and re.fullmatch(
            r'(?i)\s*(stop|unsubscribe|remove me|do not contact me|don.t contact me)[.!\s]*', e['body']) for e in events)
        context = {**identity, 'events': events, 'sources': self.statuses, 'alerts': alerts,
            'checked_at': stamp(), 'lead_name': lead['name'] if lead else None,
            'terminal': bool(lead and lead.get('status') in ('dead', 'contract')),
            'superseded': superseded, 'blocked': bool(alerts) or optout,
            'optout': optout, 'related_thread_ids': sorted(t['id'] for t in related)}
        stable = {k: v for k, v in context.items() if k not in ('checked_at', 'sources')}
        stable['source_complete'] = [(s['source'], s['complete']) for s in self.statuses]
        context['fingerprint'] = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
        return {**thread, 'cross_channel': context}


def build_context(threads, db_path=db.DB_PATH):
    local = db.email_context_data(db_path)
    twilio, twilio_status = read_twilio()
    voice, voice_status = read_voice_export()
    return ContextIndex(local, twilio + voice, [twilio_status, voice_status], threads)


def notification_advice(thread):
    """A notification can require attention without being an email to reply to."""
    latest = thread['messages'][-1]
    senders = addresses(latest.get('from'))
    if not senders or MAILBOX in senders or any(relay_phone(a) for a in senders):
        return None
    sender = senders[0]
    automated = any(token in sender.split('@')[0] for token in ('noreply', 'no-reply', 'mailer-daemon'))
    automated = automated or sender == 'listings@redfin.com' or bool(latest.get('list-unsubscribe'))
    if not automated:
        return None
    subject = latest.get('subject', '(No subject)')
    attention = 'voice-noreply@google.com' == sender or any(word in subject.lower() for word in
                 ('suspended', 'failure', 'security', 'action required', 'missed call', 'voicemail'))
    return {'summary': subject, 'reply_reason': 'Review this notification in its source app; do not reply to the automated sender.',
            'draft_text': '', 'review_state': 'needs_review' if attention else 'no_reply_needed'}
