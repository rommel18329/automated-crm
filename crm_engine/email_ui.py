"""Streamlit email review page, kept outside the main dashboard."""
import json
from datetime import datetime, timezone

import streamlit as st

from crm_engine import db
from crm_engine.email_review import approve, email_lock, save_edit, store_thread, sync
from crm_engine.gmail_client import GmailClient, LOCAL, MAILBOX, recipient_for


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
    st.header('Email Review')
    st.caption(MAILBOX + ' · Suggested replies for your approval')
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
    pending = sum(r['review_state'] == 'needs_review' for r in rows)
    st.metric('Awaiting your review', pending)
    st.caption('Unanswered conversations carry forward each day. AI uses email text to prepare suggestions; '
               'attachments are listed but their contents are not analyzed.')
    selected = st.radio('Show', ['Needs review', 'No reply needed', 'Dismissed', 'Answered', 'Send history'], horizontal=True)
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
    states = {'Needs review': 'needs_review', 'No reply needed': 'no_reply_needed',
              'Dismissed': 'dismissed', 'Answered': 'answered'}
    visible = [r for r in rows if r['review_state'] == states[selected]]
    if not visible:
        st.info('No conversations in this view.')
        return
    active_attempts = {(a['thread_id'], a['basis_message_id']): a for a in db.email_attempts(MAILBOX)
                       if a['send_state'] in ('sending', 'accepted', 'unknown')}
    for row in visible:
        thread = json.loads(row['thread_json'])
        latest = thread['messages'][-1]
        key = 'email_' + row['thread_id'] + '_' + row['latest_message_id']
        with st.container(border=True):
            st.subheader(latest.get('subject') or '(No subject)')
            st.write(latest.get('from', 'Unknown sender'))
            st.caption(latest['timestamp'])
            st.write(row['summary'])
            st.write(row['reply_reason'])
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
            if send_col.button('Approve & send', type='primary', key=key + '_send', disabled=not text.strip()):
                action = 'send'
            if save_col.button('Save edit', key=key + '_save'):
                action = 'save'
            if deny_col.button('Deny', key=key + '_deny'):
                action = 'deny'
            if regenerate_col.button('Suggest again', key=key + '_generate'):
                action = 'generate'
            if action:
                try:
                    with st.spinner('Processing…'), email_lock():
                        if action == 'send':
                            mid = approve(GmailClient.connect(), row['thread_id'], row['latest_message_id'],
                                          text, st.session_state.get('user', 'local user'))
                            st.session_state['email_notice'] = 'Gmail accepted your reply. Message ID: ' + mid
                        elif action == 'generate':
                            client = GmailClient.connect()
                            store_thread(client.get_thread(row['thread_id']), force=True)
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
