import base64
from email import policy
from email.parser import BytesParser
from unittest.mock import MagicMock

import pytest

from crm_engine.gmail_client import GmailClient, MAILBOX, ConnectionRequired, normalize_thread, recipient_for


def b64(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip('=')


def test_mime_html_attachments_and_drafts():
    raw = {'id': 't1', 'messages': [
        {'id': 'm1', 'internalDate': '1234567890000', 'labelIds': ['INBOX'], 'payload': {
            'headers': [{'name': 'From', 'value': 'seller@example.com'}], 'parts': [
                {'mimeType': 'text/html', 'body': {'data': b64('<p>Hello &amp; goodbye</p><script>evil()</script>')}},
                {'filename': 'property.pdf', 'mimeType': 'application/pdf', 'body': {'attachmentId': 'a1'}}]}},
        {'id': 'draft', 'internalDate': '1234567891000', 'labelIds': ['DRAFT'], 'payload': {}}
    ]}
    result = normalize_thread(raw)
    assert len(result['messages']) == 1
    assert 'Hello & goodbye' in result['messages'][0]['body']
    assert 'evil()' not in result['messages'][0]['body']
    assert result['messages'][0]['attachments'] == ['property.pdf']


def test_pagination_fetches_all_pages():
    service = MagicMock()
    service.users().threads().list().execute.side_effect = [
        {'threads': [{'id': 'a'}], 'nextPageToken': 'page2'}, {'threads': [{'id': 'b'}]}]
    client = GmailClient(service)
    assert list(client.list_threads('query')) == ['a', 'b']
    assert service.users().threads().list.call_args.kwargs['pageToken'] == 'page2'


def test_reject_wrong_identity():
    service = MagicMock()
    service.users().getProfile().execute.return_value = {'emailAddress': 'other@example.com'}
    with pytest.raises(ConnectionRequired):
        GmailClient(service).verify_identity()


@pytest.mark.parametrize('sender', ['a@example.com,b@example.com', MAILBOX, 'bad\r\nBcc: x@example.com'])
def test_reject_ambiguous_or_injected_recipient(sender):
    with pytest.raises(ValueError):
        recipient_for({'from': sender})


def test_reply_headers_and_no_automatic_send_retry():
    service = MagicMock()
    service.users().messages().send().execute.return_value = {'id': 'sent'}
    client = GmailClient(service)
    thread = {'id': 't1', 'messages': [{'message-id': '<parent@example.com>', 'subject': 'The house',
                                       'references': '<older@example.com>'}]}
    client.send_reply(thread, 'owner@example.com', 'My approved reply', '<unique@silverlineinvestment.group>')
    body = service.users().messages().send.call_args.kwargs['body']
    message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(body['raw']))
    assert body['threadId'] == 't1'
    assert message['From'] == MAILBOX
    assert message['To'] == 'owner@example.com'
    assert message['In-Reply-To'] == '<parent@example.com>'
    assert '<older@example.com>' in message['References']
    assert message['Subject'] == 'Re: The house'
    assert message['Bcc'] is None and message['Cc'] is None
    assert message.get_content().strip() == 'My approved reply'
    service.users().messages().send().execute.assert_called_once_with(num_retries=0)


def test_large_inline_body_is_read_but_named_attachment_is_not():
    service = MagicMock()
    service.users().threads().get().execute.return_value = {'id': 't', 'messages': [
        {'id': 'm', 'internalDate': '1234567890000', 'payload': {'parts': [
            {'mimeType': 'text/plain', 'body': {'attachmentId': 'inline'}},
            {'mimeType': 'text/plain', 'filename': 'private.txt', 'body': {'attachmentId': 'file'}}]}}]}
    service.users().messages().attachments().get().execute.return_value = {'data': b64('Long inline body')}
    client = GmailClient(service)
    result = client.get_thread('t')
    assert result['messages'][0]['body'] == 'Long inline body'
    assert result['messages'][0]['attachments'] == ['private.txt']
    assert service.users().messages().attachments().get.call_args.kwargs['id'] == 'inline'


def test_reopening_our_outbound_uses_the_external_recipient():
    assert recipient_for({'from': MAILBOX, 'to': 'Seller <seller@example.com>', 'labels': ['SENT']}) == 'seller@example.com'


def test_batch_read_preserves_all_threads_and_falls_back_for_failed_item(monkeypatch):
    service = MagicMock()
    def batch_factory(callback):
        class Batch:
            def __init__(self): self.ids = []
            def add(self, request, request_id): self.ids.append(request_id)
            def execute(self):
                for tid in self.ids:
                    callback(tid, {'id': tid, 'messages': []}, RuntimeError('retry') if tid == 'b' else None)
        return Batch()
    service.new_batch_http_request.side_effect = batch_factory
    client = GmailClient(service)
    monkeypatch.setattr(client, 'get_thread', lambda tid: {'id': tid, 'messages': []})
    assert [t['id'] for t in client.get_threads(['a', 'b', 'c'])] == ['a', 'b', 'c']
