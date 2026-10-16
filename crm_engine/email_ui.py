"""Streamlit email review page, kept outside the main dashboard."""
import json
from datetime import datetime, timezone
from email.utils import parseaddr

import streamlit as st

from crm_engine import db
from crm_engine.email_review import (approve, email_lock, prepare_suggestion, save_edit,
                                     store_thread, suggest, sync)
from crm_engine.gmail_client import GmailClient, LOCAL, MAILBOX, recipient_for, addresses
from crm_engine.email_opportunities import analyze, period, months_ago, prepare_analysis_request
from crm_engine.catchup_sources import build_context
from crm_engine.ai_ui import priced_action

BULK_ANALYZE_LIMIT = 25


def needs_analysis(op):
    return bool(op) and (op.get('analysis_state') in ('pending_price', 'failed')
                         or bool(op.get('price_pending')))


def prepare_thread_ai(thread):
    """The paid request for one conversation, or {'result': ...} when no AI is needed."""
    if thread.get('cross_channel'):
        return prepare_analysis_request(thread)
    return prepare_suggestion(thread)


def run_thread_ai(thread, approval, prepared):
    """Run one priced analysis and store it. Caller holds email_lock."""
    if thread.get('cross_channel'):
        result = prepared['result'] if 'result' in prepared else analyze(thread, approval=approval)
        store_thread(thread, force=True, opportunity_result=result)
    else:
        advice = suggest(thread, approval=approval, prepared=prepared)
        store_thread(thread, generator=lambda _thread: advice, force=True)


def run_thread_ai_locked(thread, approval, prepared):
    with email_lock():
        run_thread_ai(thread, approval, prepared)
    return True


def setup_help():
    st.markdown('**Connect Gmail once**')
    st.write(f'Sign in as {MAILBOX}. You will grant read access and permission to send only when you approve a reply here.')
    st.markdown('[Open Google Cloud credentials](https://console.cloud.google.com/apis/credentials)')
    st.write('Enable the Gmail API, configure the OAuth consent screen, and create an OAuth client of type Desktop app. '
             'For a Workspace-only app, use Internal audience if available; otherwise add this mailbox as a test user.')
    st.write('Save the downloaded client JSON here (do not paste credentials in chat):')
    st.code(str(LOCAL / 'gmail-client.json'), language=None)
    uploaded = st.file_uploader('Or upload the downloaded Desktop OAuth client JSON here', type=['json'], key='email_oauth_upload')
    if uploaded is not None and st.button('Save connection settings', key='email_oauth_save'):
        try:
            config = json.loads(uploaded.getvalue())
            installed = config.get('installed', {})
            if not installed.get('client_id') or not installed.get('client_secret'):
                raise ValueError('Choose a Desktop app OAuth client JSON, not a token or service account file.')
            if installed.get('auth_uri') != 'https://accounts.google.com/o/oauth2/auth' or installed.get('token_uri') != 'https://oauth2.googleapis.com/token':
                raise ValueError('Choose the original Desktop app client JSON downloaded from Google Cloud.')
            with email_lock():
                LOCAL.mkdir(exist_ok=True)
                (LOCAL / 'gmail-client.json').write_text(json.dumps(config), encoding='utf-8')
            st.success('Connection settings saved locally.')
        except (ValueError, RuntimeError) as exc:
            st.error(str(exc))
    if (LOCAL / 'gmail-client.json').exists():
        if st.button('Connect Gmail', key='email_connect'):
            try:
                with st.spinner('Complete Google sign-in in your browser, then return here…'), email_lock():
                    sync(GmailClient.connect(interactive=True))
                st.session_state['email_notice'] = 'Gmail connected and your first email review is ready.'
                st.rerun()
            except Exception as exc:
                st.error(str(exc) if isinstance(exc, (RuntimeError, ValueError)) else
                         'Google connection did not complete. Check the account, permissions and OAuth settings, then try again.')
    with st.expander('Terminal connection alternative'):
        st.code('python -m crm_engine.email_sync --connect', language='powershell')
    st.caption('Connecting performs the first sync. It never sends a reply. See README.md for scheduler setup.')


def render_email_review():
    reset = st.session_state.pop('email_reset_widget', None)
    if reset:
        st.session_state.pop(reset, None)
    st.header('Deal & Relationship Opportunities')
    st.caption(MAILBOX + ' · Real conversations worth continuing or reopening')
    if not db.email_ready():
        st.info('The email database migration has not been applied.')
        st.code('python -m crm_engine.email_sync --migrate')
        return
    status = db.email_sync_status(MAILBOX)
    st.caption('Last successful sync: ' + (status.get('last_success_at') or 'Not synced yet'))
    if status.get('last_error'):
        st.warning(status['last_error'])
    notice = st.session_state.pop('email_notice', None)
    if notice:
        st.success(notice)
    if not (LOCAL / 'gmail-token.json').exists():
        setup_help()
        return
    with st.expander('Connection and setup'):
        setup_help()
    if st.button('Sync now', key='email_sync'):
        try:
            with st.spinner('Checking Gmail and preparing replies…'), email_lock():
                count = sync(GmailClient.connect())
            st.session_state['email_notice'] = f'Refreshed {count} conversations.'
            st.rerun()
        except Exception as exc:
            st.error(str(exc) if isinstance(exc, (RuntimeError, ValueError)) else 'Could not sync Gmail. Check connection and try again.')
    rows = db.email_reviews(MAILBOX)
    details = {r['thread_id']: json.loads(r['thread_json']) for r in rows}
    def assessment(row):
        return details[row['thread_id']].get('opportunity', {})
    pending = sum(assessment(r).get('is_opportunity', False) and r['review_state'] == 'needs_review' for r in rows)
    st.metric('Opportunities to review', pending)
    st.caption('Personal seller, buyer, agent, investor and referral conversations. Routine alerts and newsletters '
               'are excluded. Suggestions use available text/CRM history; attachments are not analyzed.')
    waiting = [r for r in rows if period(assessment(r).get('last_contact_at', r['latest_received_at'])) != 'Outside last year'
               and r['review_state'] not in ('dismissed', 'answered') and needs_analysis(assessment(r))]
    if waiting:
        st.info(f'{len(waiting)} conversations have new activity or no AI analysis yet. Sync never runs paid AI; '
                'analyze them here or one at a time below.')
        batch = sorted(waiting, key=lambda r: r['latest_received_at'], reverse=True)[:BULK_ANALYZE_LIMIT]

        def prepare_bulk():
            requests = []
            for r in batch:
                request = prepare_thread_ai(details[r['thread_id']])
                if 'result' not in request:
                    requests.append({**request, 'thread_id': r['thread_id']})
            return requests or {'no_ai': True}

        def run_bulk(approval, prepared):
            if not isinstance(prepared, list):
                return 0
            with email_lock():
                for quote, request in zip(approval, prepared):
                    thread = json.loads(db.email_review(MAILBOX, request['thread_id'])['thread_json'])
                    run_thread_ai(thread, quote, request)
            return len(prepared)

        done = priced_action('email_bulk_analyze', f'Analyze {len(batch)} newest with AI', prepare_bulk, run_bulk)
        if done is not None:
            st.session_state['email_notice'] = f'Analyzed {done} conversations.'
            st.rerun()
    scan_file = LOCAL / 'opportunity-scan.json'
    if scan_file.exists():
        try:
            scan = json.loads(scan_file.read_text(encoding='utf-8'))
            st.caption(f"Year review: {scan.get('state', 'in progress')} · {scan.get('processed', 0)} / {scan.get('total', '?')} analyzed")
            if scan.get('state') == 'reading Gmail':
                st.caption(f"Downloaded {scan.get('downloaded', 0)} conversations so far.")
        except (ValueError, OSError):
            pass
    selected_period = st.radio('Conversation window', ['Last 6 months', '6–12 months ago', 'All last 12 months'], horizontal=True)
    st.caption(f'Last 6 months starts {months_ago(6)}; full year starts {months_ago(12)}. Grouped by most recent contact.')
    selected = st.radio('Show', ['Opportunities', 'Waiting on them', 'Analysis pending', 'Dismissed', 'Excluded', 'Send history'], horizontal=True)
    if selected == 'Send history':
        attempts = db.email_attempts(MAILBOX)
        if not attempts:
            st.info('No replies have been approved here yet.')
        for a in attempts:
            with st.container(border=True):
                st.write(f"{a['recipient']} · {a['subject']}")
                st.write(f"{a['send_state'].upper()} · {a['approved_at']} · approved by {a['approved_by']}")
                st.text(a['approved_text'])
                if a['gmail_message_id']:
                    st.caption('Accepted by Gmail · message ID: ' + a['gmail_message_id'])
                if a['last_error']:
                    st.warning(a['last_error'])
        return
    def visible_row(row):
        op = assessment(row)
        stamp = op.get('last_contact_at', row['latest_received_at'])
        cohort = period(stamp)
        if cohort == 'Outside last year' or (selected_period != 'All last 12 months' and cohort != selected_period):
            return False
        if selected == 'Analysis pending':
            return not op or op.get('analysis_state') != 'complete' or bool(op.get('price_pending'))
        if selected == 'Dismissed':
            return row['review_state'] == 'dismissed'
        if selected == 'Waiting on them':
            return op.get('is_opportunity') and op.get('stage') == 'wait'
        if selected == 'Excluded':
            return bool(op) and op.get('analysis_state') == 'complete' and not op.get('is_opportunity')
        return op.get('is_opportunity') and row['review_state'] == 'needs_review' and op.get('stage') != 'wait'
    visible = sorted([r for r in rows if visible_row(r)], key=lambda r: (-assessment(r).get('priority', 0), r['latest_received_at']))
    if not visible:
        st.info('No conversations in this view.')
        return
    active_attempts = {(a['thread_id'], a['basis_message_id']): a for a in db.email_attempts(MAILBOX)
                       if a['send_state'] in ('sending', 'accepted', 'unknown')}
    for row in visible:
        thread = details[row['thread_id']]
        latest = thread['messages'][-1]
        cross = thread.get('cross_channel', {})
        op = thread.get('opportunity', {})
        key = 'email_' + row['thread_id'] + '_' + row['latest_message_id'] + '_' + cross.get('fingerprint', '')[:12]
        with st.container(border=True):
            contact = next((m.get('from', '') for m in reversed(thread['messages'])
                            if MAILBOX not in addresses(m.get('from')) and 'SENT' not in m.get('labels', [])), latest.get('to', ''))
            label, address = parseaddr(contact)
            st.subheader(cross.get('lead_name') or label or address or 'Conversation')
            st.caption(latest.get('subject') or '(No subject)')
            st.caption(latest['timestamp'])
            st.write(row['summary'])
            st.write(row['reply_reason'])
            if op.get('price_pending'):
                st.warning('New activity since this analysis. Use "Analyze with AI" to refresh it.')
            if op.get('is_opportunity'):
                st.caption(f"{op.get('category', '').replace('-', ' ')} · priority {op.get('priority', '?')}/5 · {op.get('stage', '').replace('_', ' ')} · confidence {op.get('confidence', 'unknown')}")
                st.write('Suggested next step: ' + op.get('next_step', 'Review the conversation.'))
            if cross:
                st.caption('Matched contact: ' + (cross.get('lead_name') or 'No confirmed CRM lead match') +
                           ' · checked ' + cross.get('checked_at', 'unknown'))
                if cross.get('blocked'):
                    st.warning('Draft for review only: current text history or the contact match still needs verification.')
                with st.expander('Text, call and related email context'):
                    for alert in cross.get('alerts', []):
                        st.warning(alert)
                    for source in cross.get('sources', []):
                        st.caption(source['source'] + ': ' + source['detail'])
                    for event in cross.get('events', []):
                        st.caption(f"{event['timestamp']} · {event['channel']} · {event['direction']} · {event['source']} · {event['status']}")
                        st.text(event['body'])
            st.link_button('Open conversation in Gmail',
                           'https://mail.google.com/mail/u/' + MAILBOX + '/#all/' + row['thread_id'])
            with st.expander('Conversation history'):
                for m in thread['messages']:
                    st.write(f"{m.get('from', 'Unknown')} · {m['timestamp']}")
                    st.text(m['body'] or '(No readable body)')
                    if m.get('attachments'):
                        st.warning('Attachments not analyzed: ' + ', '.join(m['attachments']))
                    st.divider()
            if any(m.get('attachments') for m in thread['messages']):
                st.caption('This conversation has attachments. Open Gmail to review them before approving.')
            if row['review_state'] in ('no_reply_needed', 'dismissed'):
                if st.button('Return to review', key=key + '_restore'):
                    try:
                        with email_lock():
                            save_edit(row['thread_id'], row['latest_message_id'], row['draft_text'])
                        st.rerun()
                    except RuntimeError as exc:
                        st.error(str(exc))
                continue
            if row['review_state'] == 'answered':
                continue
            attempt = active_attempts.get((row['thread_id'], row['latest_message_id']))
            if attempt:
                st.warning('Send status: ' + attempt['send_state'] + '. See Send history. Sync to reconcile an uncertain result.')
                continue
            try:
                recipient = recipient_for(latest)
            except ValueError as exc:
                st.warning(str(exc))
                continue
            st.write('Reply to: ' + recipient)
            st.caption('Reply to sender only. No CC or BCC recipients.')
            text = st.text_area('Suggested reply — edit before approving', value=row['draft_text'],
                                height=160, key=key + '_text')
            send_col, save_col, deny_col, regenerate_col = st.columns(4)
            action = None
            blocked = not cross or cross.get('blocked') or cross.get('kind') != 'email' or op.get('analysis_state') != 'complete'
            if cross.get('kind') == 'voice_text':
                st.info('This is a Google Voice text conversation. Review the draft, then reply in Google Voice after checking its live history.')
                st.link_button('Open Google Voice', 'https://voice.google.com/u/0/messages')
            if send_col.button('Approve & send', type='primary', key=key + '_send', disabled=not text.strip() or blocked):
                action = 'send'
            if save_col.button('Save edit', key=key + '_save'):
                action = 'save'
            if deny_col.button('Deny', key=key + '_deny'):
                action = 'deny'
            analyzed = priced_action(
                key + '_generate', 'Analyze with AI',
                lambda thread=thread: prepare_thread_ai(thread),
                lambda approval, prepared, thread=thread: run_thread_ai_locked(thread, approval, prepared),
                area=regenerate_col)
            if analyzed is not None:
                st.session_state['email_reset_widget'] = key + '_text'
                st.rerun()
            if action:
                try:
                    with st.spinner('Processing…'), email_lock():
                        if action == 'send':
                            mid = approve(GmailClient.connect(), row['thread_id'], row['latest_message_id'],
                                          text, st.session_state.get('user', 'local user'), context_basis=cross.get('fingerprint'))
                            st.session_state['email_notice'] = 'Gmail accepted your reply. Message ID: ' + mid
                        elif action == 'generate':
                            client = GmailClient.connect()
                            fetched = client.get_thread(row['thread_id'])
                            related = [json.loads(r['thread_json']) for r in db.email_reviews(MAILBOX) if r['thread_id'] != row['thread_id']] + [fetched]
                            store_thread(build_context(related).attach(fetched), force=True)
                            st.session_state['email_reset_widget'] = key + '_text'
                        else:
                            save_edit(row['thread_id'], row['latest_message_id'], text,
                                      'dismissed' if action == 'deny' else 'needs_review')
                            st.session_state['email_notice'] = 'Dismissed; no reply sent.' if action == 'deny' else 'Edit saved; no reply sent.'
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc) if isinstance(exc, (RuntimeError, ValueError)) else
                             'Could not complete this action. Check Send history before retrying a send.')


def catch_up_if_due():
    """Catch up on page load, including when the scheduled task missed a run."""
    if not db.email_ready() or not (LOCAL / 'gmail-token.json').exists():
        return
    status = db.email_sync_status(MAILBOX)
    last = status.get('last_success_at')
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() if last else float('inf')
    # Per-session throttle also avoids rerun loops while Gmail is unavailable.
    attempted = st.session_state.get('email_auto_sync_at', 0)
    current = datetime.now(timezone.utc).timestamp()
    if age >= 900 and current - attempted >= 900:
        st.session_state['email_auto_sync_at'] = current
        try:
            with st.spinner('Catching up on email…'), email_lock():
                sync(GmailClient.connect())
        except Exception:
            st.info('Email could not refresh automatically. Open Email Review to check connection and sync status.')
