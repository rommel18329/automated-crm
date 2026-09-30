"""Read-only Gmail context for SMS drafting; never imports or sends messages."""
from __future__ import annotations

import json
from itertools import islice

from crm_engine import db
from crm_engine.catchup_sources import ContextIndex, direct_text, lead_phones, utc
from crm_engine.gmail_client import GmailClient, LOCAL, MAILBOX, addresses


def _confirmed(index, thread, lead_id):
    identity = index.identity(thread)
    return (identity['lead_id'] == lead_id and not identity['ambiguous']
            and identity['kind'] == 'email' and len(identity['sender_emails']) == 1)


def load_email_context(lead, *, db_path=db.DB_PATH, connector=None, snapshot=None):
    """Match explicit contact identifiers, then refresh Gmail on each draft request.

    Cached matches support discovery and a clearly labeled fallback. Limit provider
    reads for interactive latency and disclose partial results rather than claiming
    that an incomplete search ruled out duplicate contact.
    """
    result = {'messages': [], 'status': '', 'complete': False}
    matched = {}
    limitations = []
    try:
        local = db.email_context_data(db_path)
        if lead.get('id') not in {r['id'] for r in local['leads']}:
            result['status'] = 'Email not checked: this contact is not in the CRM.'
            return result
        cached = {}
        snapshot = snapshot if snapshot is not None else LOCAL / 'year-threads.jsonl'
        if snapshot.exists():
            for line in snapshot.read_text(encoding='utf-8').splitlines():
                try:
                    thread = json.loads(line)
                    cached[thread['id']] = thread
                except (ValueError, KeyError):
                    limitations.append('Some cached email data could not be read.')
        for row in db.email_reviews(MAILBOX, db_path):
            thread = json.loads(row['thread_json'])
            if thread.get('messages'):
                previous = cached.get(thread['id'], {}).get('messages', [])
                if not previous or thread['messages'][-1]['timestamp'] >= previous[-1]['timestamp']:
                    cached[thread['id']] = thread
        index = ContextIndex(local, [], [], [])
        known = {email for email, ids in index.email_leads.items() if ids == {lead['id']}}
        for thread in cached.values():
            if thread.get('messages') and _confirmed(index, thread, lead['id']):
                matched[thread['id']] = thread
                known.update(index.identity(thread)['sender_emails'])
        phones = lead_phones(lead)
        if not known and not phones:
            result['status'] = 'Email not checked: no confirmed email address or phone is available. Names alone are not used.'
            return result
        try:
            # Reuse the Gmail lock to avoid racing token refresh / background sync.
            from crm_engine.email_review import email_lock
            with email_lock():
                client = (connector or GmailClient.connect)()
                terms = [f'from:{email} to:{email}' for email in sorted(known)]
                for number in sorted(phones):
                    digits = number[-10:]
                    terms.extend([f'"{digits}"', f'"{digits[:3]}-{digits[3:6]}-{digits[6:]}"',
                                  f'"({digits[:3]}) {digits[3:6]}-{digits[6:]}"'])
                query = '{' + ' '.join(terms) + '} -in:spam -in:trash -in:drafts'
                ids = list(islice(client.list_threads(query), 41))
                if len(ids) > 40:
                    limitations.append('Search limited to the 40 newest matching Gmail threads.')
                # Addresses established by a unique phone match remain linked across threads.
                for email in known:
                    index.email_leads.setdefault(email, set()).add(lead['id'])
                fetched = list(client.get_threads(ids[:40]))
                discovered = set()
                for thread in fetched:
                    if thread.get('messages') and _confirmed(index, thread, lead['id']):
                        discovered.update(index.identity(thread)['sender_emails'])
                for email in discovered:
                    index.email_leads.setdefault(email, set()).add(lead['id'])
                # A phone signature can establish an address absent from the CRM.
                # Search that address too, so threads without its signature count.
                if discovered - known:
                    email_query = '{' + ' '.join(f'from:{a} to:{a}' for a in sorted(discovered - known)) + '} -in:spam -in:trash -in:drafts'
                    extra_ids = list(islice(client.list_threads(email_query), 41))
                    if len(extra_ids) > 40:
                        limitations.append('Additional address search limited to 40 threads.')
                    fetched.extend(client.get_threads([tid for tid in extra_ids[:40] if tid not in ids[:40]]))
                for thread in fetched:
                    matched.pop(thread['id'], None)
                    if thread.get('messages') and _confirmed(index, thread, lead['id']):
                        matched[thread['id']] = thread
                if set(matched) - {t['id'] for t in fetched}:
                    limitations.append('Some historical matched threads are cached and were not refreshed.')
                result['complete'] = not limitations
        except Exception:
            limitations.append('Gmail could not be refreshed. Any email shown is cached; newer communication may be missing.')
    except Exception:
        limitations.append('Email context could not be fully checked.')

    messages = {}
    for thread in matched.values():
        for message in thread.get('messages', []):
            if 'DRAFT' in message.get('labels', []):
                continue
            outgoing = MAILBOX in addresses(message.get('from')) or 'SENT' in message.get('labels', [])
            messages[message['id']] = {
                'id': message['id'], 'type': 'email', 'timestamp': utc(message['timestamp']),
                'direction': 'outbound' if outgoing else 'inbound',
                'content': direct_text(message.get('body', '')),
                'subject': message.get('subject', ''),
            }
    ordered = sorted(messages.values(), key=lambda m: (m['timestamp'], m['id']))
    if len(ordered) > 20:
        limitations.append('Draft context includes the latest 20 matched emails.')
    ordered = ordered[-20:]
    if any(len(m['content']) > 2000 for m in ordered):
        limitations.append('Long email bodies are shortened to 2,000 characters; attachments are not read.')
    for message in ordered:
        message['content'] = message['content'][:2000]
    result['messages'] = ordered
    result['complete'] = result['complete'] and not limitations
    if limitations:
        result['status'] = 'Email check incomplete. ' + ' '.join(dict.fromkeys(limitations))
    elif ordered:
        result['status'] = f'Gmail checked: {len(ordered)} emails across {len(matched)} confirmed contact threads. Attachments not read.'
    else:
        result['status'] = 'Gmail checked: no confidently matched email conversations found. This does not prove there are none; names alone are not matched.'
    return result


def format_email_context(context):
    lines = [context['status']]
    for item in context['messages']:
        who = 'YOU' if item['direction'] == 'outbound' else 'CONTACT'
        lines.append(f"[{item['timestamp']}] EMAIL {who} | {item['subject']}\n{item['content']}")
    return '\n\n'.join(lines)
