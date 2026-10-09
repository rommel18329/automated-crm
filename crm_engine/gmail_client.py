"""Gmail transport. No send happens during authentication or synchronization."""
from __future__ import annotations

import base64
import os
import re
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from email.message import Message
from email.utils import getaddresses
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / '.email-local'
MAILBOX = 'contactus@silverlineinvestment.group'
GV_LINE = '17732178686'   # our Google Voice number (the "Me" line)


def gv_relay_address(their_number):
    """Mail to this address is delivered by Google Voice as an SMS FROM our
    GV number: <gv-line>.<their-number>@txt.voice.google.com."""
    d = re.sub(r'\D', '', their_number or '')
    if len(d) == 10:
        d = '1' + d
    if len(d) != 11:
        raise ValueError(f'bad GV recipient number: {their_number!r}')
    return f'{GV_LINE}.{d}@txt.voice.google.com'
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly',
          'https://www.googleapis.com/auth/gmail.send']


class ConnectionRequired(RuntimeError):
    pass


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if tag in ('p', 'div', 'br', 'li', 'tr'):
            self.chunks.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.chunks.append(data)


def addresses(value):
    return [a.lower() for _, a in getaddresses([value or '']) if a]


def recipient_for(message):
    outgoing = MAILBOX in addresses(message.get('from')) or 'SENT' in message.get('labels', [])
    value = message.get('to', '') if outgoing else message.get('reply-to') or message.get('from', '')
    recipients = addresses(value)
    if len(recipients) != 1 or '@' not in recipients[0] or any(c in value for c in '\r\n'):
        raise ValueError('This message does not have one valid reply address. Reply in Gmail.')
    if recipients[0] == MAILBOX:
        raise ValueError('Cannot reply to this mailbox itself.')
    return recipients[0]


def normalize_thread(raw):
    messages = []
    for msg in raw.get('messages', []):
        payload = msg.get('payload', {})
        headers = {h['name'].lower(): h['value'] for h in payload.get('headers', [])}
        plain, html, attachments = [], [], []

        def walk(part):
            if part.get('filename'):
                attachments.append(part['filename'])
                return
            data = part.get('body', {}).get('data')
            if data:
                charset_header = Message()
                charset_header['Content-Type'] = next((h['value'] for h in part.get('headers', [])
                    if h['name'].lower() == 'content-type'), part.get('mimeType', 'text/plain'))
                charset = charset_header.get_content_charset() or 'utf-8'
                raw_body = base64.urlsafe_b64decode(data + '=' * (-len(data) % 4))
                try:
                    content = raw_body.decode(charset, errors='replace')
                except LookupError:
                    content = raw_body.decode('utf-8', errors='replace')
                if part.get('mimeType') == 'text/plain':
                    plain.append(content)
                elif part.get('mimeType') == 'text/html':
                    parser = _Text()
                    parser.feed(content)
                    html.append(''.join(parser.chunks))
            for child in part.get('parts', []):
                walk(child)

        walk(payload)
        messages.append({**headers, 'id': msg['id'], 'labels': msg.get('labelIds', []),
                         'timestamp': datetime.fromtimestamp(int(msg['internalDate']) / 1000, timezone.utc).isoformat(),
                         'body': '\n'.join(plain or html), 'attachments': attachments})
    messages.sort(key=lambda m: (m['timestamp'], m['id']))
    # Unsent drafts must not make an unanswered conversation look answered.
    messages = [m for m in messages if 'DRAFT' not in m['labels']]
    return {'id': raw['id'], 'messages': messages}


class GmailClient:
    def __init__(self, service=None):
        self.service = service

    @classmethod
    def connect(cls, interactive=False):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        import google_auth_httplib2
        import httplib2
        LOCAL.mkdir(exist_ok=True)
        token = LOCAL / 'gmail-token.json'
        credentials = None
        if token.exists():
            credentials = Credentials.from_authorized_user_file(str(token), SCOPES)
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        if not credentials or not credentials.valid:
            if not interactive:
                raise ConnectionRequired('Connect Gmail first using the setup instructions below.')
            config = LOCAL / 'gmail-client.json'
            if not config.exists():
                raise ConnectionRequired(f'Place the Google Desktop OAuth client file at {config}')
            flow = InstalledAppFlow.from_client_secrets_file(str(config), SCOPES)
            credentials = flow.run_local_server(port=0, timeout_seconds=180,
                                               login_hint=MAILBOX, prompt='consent')
        if not credentials.has_scopes(SCOPES):
            raise ConnectionRequired('Reconnect Gmail and grant both read and send permissions.')
        http = google_auth_httplib2.AuthorizedHttp(credentials, http=httplib2.Http(timeout=45))
        result = cls(build('gmail', 'v1', http=http, cache_discovery=False))
        result.verify_identity()
        temp = token.with_suffix('.tmp')
        temp.write_text(credentials.to_json(), encoding='utf-8')
        os.replace(temp, token)
        return result

    def verify_identity(self):
        address = self.service.users().getProfile(userId='me').execute()['emailAddress'].lower()
        if address != MAILBOX:
            raise ConnectionRequired(f'Sign in as {MAILBOX}. Connected account is {address}. Aliases/shared delegation require separate setup.')
        return address

    def list_threads(self, query):
        token = None
        while True:
            response = self.service.users().threads().list(userId='me', q=query,
                        maxResults=100, pageToken=token, includeSpamTrash=False).execute()
            yield from (t['id'] for t in response.get('threads', []))
            token = response.get('nextPageToken')
            if not token:
                break

    def get_thread(self, thread_id):
        from googleapiclient.errors import HttpError
        for attempt in range(6):
            try:
                raw = self.service.users().threads().get(userId='me', id=thread_id, format='full').execute(num_retries=0)
                break
            except HttpError as exc:
                limited = exc.resp.status == 429 or (exc.resp.status == 403 and b'quota' in exc.content.lower())
                if not limited or attempt == 5:
                    raise
                time.sleep(32)
        return self._normalize_raw(raw)

    def get_threads(self, thread_ids):
        """Batch read 25 threads at a time; surface every failed fetch to the caller."""
        for start in range(0, len(thread_ids), 25):
            if start:
                time.sleep(20)  # Leave headroom under the newer per-user Gmail query-cost quota.
            results, errors = {}, {}
            def receive(request_id, response, exception):
                if exception:
                    errors[request_id] = exception
                else:
                    results[request_id] = response
            batch = self.service.new_batch_http_request(callback=receive)
            chunk = thread_ids[start:start + 25]
            for tid in chunk:
                batch.add(self.service.users().threads().get(userId='me', id=tid, format='full'), request_id=tid)
            batch.execute()
            for tid in chunk:
                if tid in errors:
                    # A fresh individual read retries transient batch failures.
                    yield self.get_thread(tid)
                else:
                    yield self._normalize_raw(results[tid])

    def _normalize_raw(self, raw):
        for message in raw.get('messages', []):
            def load_body(part):
                body = part.get('body', {})
                # Gmail can store large inline text bodies behind an attachmentId.
                # Named attachments remain unread and are disclosed in the UI.
                if not part.get('filename') and part.get('mimeType') in ('text/plain', 'text/html') and body.get('attachmentId') and not body.get('data'):
                    response = self.service.users().messages().attachments().get(
                        userId='me', messageId=message['id'], id=body['attachmentId']).execute()
                    body['data'] = response.get('data', '')
                for child in part.get('parts', []):
                    load_body(child)
            load_body(message.get('payload', {}))
        return normalize_thread(raw)

    def send_reply(self, thread, recipient, text, rfc_message_id):
        latest = thread['messages'][-1]
        parent = latest.get('message-id', '')
        if not parent:
            raise ValueError('Missing original Message-ID; reply in Gmail to preserve threading.')
        msg = EmailMessage()
        msg['From'] = MAILBOX
        msg['To'] = recipient
        subject = latest.get('subject', '')
        msg['Subject'] = subject if subject.lower().startswith('re:') else 'Re: ' + subject
        msg['Message-ID'] = rfc_message_id
        msg['In-Reply-To'] = parent
        msg['References'] = (latest.get('references', '') + ' ' + parent).strip()
        msg.set_content(text)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode('ascii')
        # Never enable automatic retries on a non-idempotent send.
        return self.service.users().messages().send(userId='me', body={
            'threadId': thread['id'], 'raw': raw}).execute(num_retries=0)

    def find_sent(self, rfc_message_id):
        response = self.service.users().messages().list(userId='me',
            q='in:sent rfc822msgid:' + rfc_message_id.strip('<>')).execute()
        return next(iter(response.get('messages', [])), None)

    def send_gv_text(self, their_number, body):
        """Send an SMS through Google Voice by emailing its relay address, so the
        recipient sees the same GV number they already know (no Twilio 'new
        number'). Returns the Gmail send response."""
        to = gv_relay_address(their_number)
        msg = EmailMessage()
        msg['To'] = to
        msg['Subject'] = ''
        msg.set_content(body)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode('ascii')
        # Non-idempotent send: never auto-retry.
        return self.service.users().messages().send(
            userId='me', body={'raw': raw}).execute(num_retries=0)
