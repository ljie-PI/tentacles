"""IMAP ingestion via Service; the only mail double is its connection factory."""
import asyncio
import imaplib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from email.policy import SMTP
from typing import Any

import pytest

from tentacles.service import TentaclesService

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


def mail(*, subject='Research update', body='New findings', message_id='<one@example.test>',
         sender='Alice <alice@example.test>', html=None, attachment=None):
    msg = EmailMessage()
    if subject is not None:
        msg['Subject'] = subject
    if message_id is not None:
        msg['Message-ID'] = message_id
    msg['From'], msg['To'] = sender, 'Me <me@example.test>'
    msg['Date'] = 'Sun, 25 Oct 2026 10:30:00 +0800'
    if body is not None:
        msg.set_content(body)
    if html is not None:
        if body is None:
            msg.set_content(html, subtype='html')
        else:
            msg.add_alternative(html, subtype='html')
    if attachment:
        msg.add_attachment(b'PRIVATE ATTACHMENT BODY', maintype='application', subtype='octet-stream',
                           filename=attachment)
    return msg.as_bytes(policy=SMTP)


def imap_config(**options):
    return {'sources': [{'id': 'inbox', 'type': 'imap', 'schedule': '30m', 'host': 'mail.example.test',
                         'username': 'me@example.test', 'password': '${MAIL_PASSWORD}', **options}]}


@dataclass
class Mailbox:
    validity: int = 10
    messages: dict[int, bytes] = field(default_factory=dict)
    # SEARCH can deliberately return duplicates, old boundaries or unordered UIDs.
    search_uids: list[int] | None = None


@dataclass
class FakeIMAP:
    folders: dict[str, Mailbox] = field(default_factory=lambda: {'"INBOX"': Mailbox()})
    events: list[tuple] = field(default_factory=list)
    selected: str = ''
    error_at: str | None = None

    def factory(self, host, port, *, ssl, timeout):
        self.events.append(('connect', host, port, ssl, timeout))
        if self.error_at == 'connect':
            raise OSError('failed using secret-app-password')
        return self

    def login(self, username, password):
        assert username == 'me@example.test' and password == 'secret-app-password'
        self.events.append(('login',))
        if self.error_at == 'login':
            raise imaplib.IMAP4.error(b'bad secret-app-password')
        return 'OK', [b'Logged in']

    def select(self, mailbox='INBOX', readonly=False):
        assert readonly is True, 'must never select a writable mailbox'
        self.events.append(('select', mailbox, readonly))
        self.selected = mailbox
        return 'OK', [str(len(self.folders[mailbox].messages)).encode()]

    def response(self, code):
        assert code == 'UIDVALIDITY'
        return code, [str(self.folders[self.selected].validity).encode()]

    def uid(self, command, *args):
        self.events.append((command, self.selected, *args))
        box = self.folders[self.selected]
        if command == 'SEARCH':
            return 'OK', [b' '.join(str(u).encode() for u in (
                box.search_uids if box.search_uids is not None else list(box.messages)))]
        assert command == 'FETCH' and args[1] == '(UID BODY.PEEK[])', 'must fetch by UID with PEEK'
        uid = int(args[0])
        body = box.messages.get(uid)
        if body is None:
            return 'OK', [None]
        return 'OK', [(f'1 (UID {uid} BODY[] {{{len(body)}}}'.encode(), body), b')']

    def logout(self):
        self.events.append(('logout',))
        return 'BYE', [b'Goodbye']

    def shutdown(self):
        self.events.append(('shutdown',))


def test_imap_fetch_is_read_only_incremental_sorted_bounded_and_not_content_deduped(write_config):
    fake = FakeIMAP({'"INBOX"': Mailbox(messages={
        3: mail(message_id='<three>'), 1: mail(message_id='<one>'), 2: mail(message_id='<two>')})})
    path = write_config(imap_config(max_fetch=2, initial_days=7, search='UNSEEN'))

    async def scenario():
        service = TentaclesService.open(path, env={'MAIL_PASSWORD': 'secret-app-password'},
                                        imap_factory=fake.factory, clock=lambda: NOW)
        try:
            first = (await service.fetch_sources(['inbox']))[0]
            assert first.status == 'ok' and first.new == 2 and first.dup == 0
            assert {service.get_item(i.id).external_id for i in service.list_items().items} == {'<one>', '<two>'}
            second = (await service.fetch_sources(['inbox']))[0]
            assert second.status == 'ok' and second.new == 1
            assert service.list_items(kinds=['email']).total == 3
            third = (await service.fetch_sources(['inbox']))[0]
            assert third.new == 0
            assert [e[2] for e in fake.events if e[0] == 'FETCH'] == ['1', '2', '3']
            searches = [e for e in fake.events if e[0] == 'SEARCH']
            assert searches[0][2:] == (None, 'SINCE', '18-Oct-2026', 'UNSEEN')
            assert searches[1][2:] == (None, 'UID', '3:*', 'UNSEEN')
            assert searches[2][2:] == (None, 'UID', '4:*', 'UNSEEN')
            assert [e for e in fake.events if e[0] == 'logout'] == [('logout',)] * 3
            assert next(e for e in fake.events if e[0] == 'connect') == (
                'connect', 'mail.example.test', 993, True, 30)
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_multipart_decodes_headers_prefers_plain_skips_attachments_and_strips_quotes(write_config):
    raw = mail(subject='研发进展', sender='爱丽丝 <alice@example.test>', message_id='<mime>',
               body='Fresh research\n> older inline quotation\nNext point\n\nOn Tue, Alice wrote:\nOld reply body',
               html='<p>SHOULD NOT USE HTML ALTERNATIVE</p>', attachment='实验数据.bin')
    fake = FakeIMAP({'"INBOX"': Mailbox(messages={1: raw})})
    path = write_config(imap_config(max_body_chars=100, max_summary_chars=18))

    async def scenario():
        service = TentaclesService.open(path, env={'MAIL_PASSWORD': 'secret-app-password'},
                                        imap_factory=fake.factory, clock=lambda: NOW)
        try:
            outcome = (await service.fetch_sources())[0]
            assert outcome.status == 'ok' and outcome.new == 1
            item = service.get_item(service.list_items().items[0].id)
            assert item.title == '研发进展' and item.author == '爱丽丝 <alice@example.test>'
            assert item.content == 'Fresh research\nNext point'
            assert item.summary == 'Fresh research Ne…'
            assert item.published == datetime(2026, 10, 25, 2, 30, tzinfo=timezone.utc)
            assert item.meta['attachments'] == ['实验数据.bin']
            assert item.meta['to'] == 'Me <me@example.test>'
            assert (item.meta['folder'], item.meta['uidvalidity'], item.meta['uid']) == ('INBOX', 10, 1)
            assert 'PRIVATE ATTACHMENT' not in json_text(item)
        finally:
            await service.aclose()

    asyncio.run(scenario())


def json_text(item):
    return item.model_dump_json()


def test_html_only_and_attached_plain_mail_fallbacks_and_body_limits(write_config):
    msg = EmailMessage()
    msg['From'] = 'Alice <alice@example.test>'
    msg['Date'] = 'not a date'
    msg.set_content('<p>Fresh <b>report</b> details</p><div class="gmail_quote">'
                    '<blockquote>Old HTML reply</blockquote></div><script>Secret script</script>', subtype='html')
    msg.add_attachment('ATTACHED PLAIN BODY', filename='notes.txt', disposition='inline')
    attached = EmailMessage()
    attached.set_content('ATTACHED MESSAGE BODY')
    msg.add_attachment(attached, filename='forward.eml')
    fake = FakeIMAP({'"INBOX"': Mailbox(messages={42: msg.as_bytes(policy=SMTP)})})
    path = write_config(imap_config(max_body_chars=12, max_summary_chars=8))

    async def scenario():
        service = TentaclesService.open(path, env={'MAIL_PASSWORD': 'secret-app-password'},
                                        imap_factory=fake.factory, clock=lambda: NOW)
        try:
            assert (await service.fetch_sources())[0].new == 1
            item = service.get_item(service.list_items().items[0].id)
            assert item.title == '(no subject)' and item.external_id == 'INBOX:10:42'
            assert item.content == 'Fresh repor…' and item.summary == 'Fresh r…'
            assert item.published is None and item.meta['attachments'] == ['notes.txt', 'forward.eml']
            assert not any(s in json_text(item) for s in ['ATTACHED', 'Old HTML', 'Secret script'])
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_excluded_senders_advance_bounded_folder_cursors_and_validity_reset_survives_restart(write_config):
    fake = FakeIMAP({
        '"INBOX"': Mailbox(messages={2: mail(message_id=None),
                                     1: mail(message_id='<spam>', sender='SPAM <Spam@Example.Test>')}),
        '"Archive"': Mailbox(validity=20, messages={1: mail(message_id=None)}),
    })
    path = write_config(imap_config(folders=['INBOX', 'Archive'], max_fetch=1,
                                    exclude_senders=['spam@example.test']))

    async def open_and_fetch():
        service = TentaclesService.open(path, env={'MAIL_PASSWORD': 'secret-app-password'},
                                        imap_factory=fake.factory, clock=lambda: NOW)
        try:
            outcome = (await service.fetch_sources())[0]
            ids = {service.get_item(i.id).external_id for i in service.list_items().items}
            return outcome, ids
        finally:
            await service.aclose()

    async def scenario():
        first, ids = await open_and_fetch()
        assert first.status == 'ok' and first.new == 0 and first.stats['excluded'] == 1
        assert first.stats['processed'] == 1 and ids == set()
        second, ids = await open_and_fetch()
        assert second.new == 1 and ids == {'INBOX:10:2'}
        third, ids = await open_and_fetch()
        assert third.new == 1 and ids == {'INBOX:10:2', 'Archive:20:1'}
        fake.folders['"INBOX"'].validity = 11
        fake.folders['"INBOX"'].messages = {1: mail(message_id=None)}
        fourth, ids = await open_and_fetch()
        assert fourth.new == 1 and ids == {'INBOX:10:2', 'INBOX:11:1', 'Archive:20:1'}
        search = [e for e in fake.events if e[0] == 'SEARCH' and e[1] == '"INBOX"']
        assert [e[2:5] for e in search] == [(None, 'SINCE', '25-Sep-2026'), (None, 'UID', '2:*'),
                                          (None, 'UID', '3:*'), (None, 'SINCE', '25-Sep-2026')]

    asyncio.run(scenario())


def test_unicode_and_quoted_folders_use_modified_utf7_and_preserve_original_identity(write_config):
    names = ['A&B "folder"', '~peter/mail/台北/日本語']
    fake = FakeIMAP({
        '"A&-B \\"folder\\""': Mailbox(messages={1: mail(message_id=None)}),
        '"~peter/mail/&U,BTFw-/&ZeVnLIqe-"': Mailbox(messages={1: mail(message_id=None)}),
    })
    path = write_config(imap_config(folders=names, ssl=False, port=1143, timeout=9))

    async def scenario():
        service = TentaclesService.open(path, env={'MAIL_PASSWORD': 'secret-app-password'},
                                        imap_factory=fake.factory, clock=lambda: NOW)
        try:
            outcome = (await service.fetch_sources())[0]
            assert outcome.status == 'ok' and outcome.new == 2
            assert {service.get_item(i.id).meta['folder'] for i in service.list_items().items} == set(names)
            assert fake.events[0] == ('connect', 'mail.example.test', 1143, False, 9)
        finally:
            await service.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['connect', 'login'])
def test_imap_connection_and_auth_errors_are_safe_and_other_sources_continue(write_config, web, caplog, failure):
    from conftest import rss_feed
    config = imap_config()
    config['sources'].append({'id': 'news', 'type': 'rss', 'url': 'https://news.test/rss', 'schedule': '30m'})
    web.serve('https://news.test/rss', rss_feed({'title': 'News item', 'guid': 'news'}))
    fake = FakeIMAP(error_at=failure)
    path = write_config(config)

    async def scenario():
        service = TentaclesService.open(path, env={'MAIL_PASSWORD': 'secret-app-password'},
                                        imap_factory=fake.factory, transport=web.transport(), clock=lambda: NOW)
        try:
            outcomes = {o.source_id: o for o in await service.fetch_sources()}
            assert outcomes['news'].new == 1 and outcomes['inbox'].status == 'error'
            assert outcomes['inbox'].error.startswith('IMAP fetch failed (')
            runs = service.list_runs(kind='fetch', target='inbox')
            assert runs[0].status == 'failed' and runs[0].error == outcomes['inbox'].error
            assert 'secret-app-password' not in (json_text(outcomes['inbox']) + json_text(runs[0]) + caplog.text)
            assert ('logout',) in fake.events if failure == 'login' else ('logout',) not in fake.events
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_failure_does_not_advance_mail_cursor_or_store_partial_batch(write_config):
    fake = FakeIMAP({'"INBOX"': Mailbox(messages={1: mail(message_id='<one>'), 2: mail(message_id='<two>')})})
    original = fake.uid
    fail = [True]

    def uid(command, *args):
        if command == 'FETCH' and args[0] == '2' and fail[0]:
            raise imaplib.IMAP4.abort('server echoed secret-app-password')
        return original(command, *args)

    fake.uid = uid
    path = write_config(imap_config())

    async def scenario():
        service = TentaclesService.open(path, env={'MAIL_PASSWORD': 'secret-app-password'},
                                        imap_factory=fake.factory, clock=lambda: NOW)
        try:
            failed = (await service.fetch_sources())[0]
            assert failed.status == 'error' and service.list_items().total == 0
            fail[0] = False
            retry = (await service.fetch_sources())[0]
            assert retry.status == 'ok' and retry.new == 2
            searches = [e for e in fake.events if e[0] == 'SEARCH']
            assert all(e[3] == 'SINCE' for e in searches)
            assert 'secret-app-password' not in failed.error
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_canceled_imap_worker_drains_logout_and_does_not_block_other_sources(write_config, web):
    import threading
    from conftest import rss_feed
    config = imap_config()
    config['sources'].append({'id': 'news', 'type': 'rss', 'url': 'https://news.test/rss', 'schedule': '30m'})
    web.serve('https://news.test/rss', rss_feed({'title': 'News item', 'guid': 'news'}))
    fake = FakeIMAP({'"INBOX"': Mailbox(messages={1: mail()})})
    entered, release = threading.Event(), threading.Event()
    original = fake.uid

    def uid(command, *args):
        if command == 'FETCH':
            entered.set()
            assert release.wait(5), 'test did not release IMAP worker'
        return original(command, *args)

    fake.uid = uid
    path = write_config(config)

    async def scenario():
        service = TentaclesService.open(path, env={'MAIL_PASSWORD': 'secret-app-password'},
                                        imap_factory=fake.factory, transport=web.transport(), clock=lambda: NOW)
        pending = None
        try:
            pending = asyncio.create_task(service.fetch_sources(['inbox']))
            assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 3), 4)
            assert (await asyncio.wait_for(service.fetch_sources(['news']), 2))[0].new == 1
            pending.cancel()
            await asyncio.sleep(0)
            pending.cancel()
            assert not pending.done() and ('logout',) not in fake.events
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(pending, 3)
            assert ('logout',) in fake.events and service.list_items(kinds=['email']).total == 0
            assert service.list_runs(kind='fetch', target='inbox')[0].status == 'failed'
            assert (await service.fetch_sources(['inbox']))[0].new == 1
        finally:
            release.set()
            if pending is not None and not pending.done():
                try:
                    await pending
                except asyncio.CancelledError:
                    pass
            await service.aclose()

    asyncio.run(scenario())


def test_password_controls_are_invalid_without_echoing_the_secret(write_config):
    from tentacles.errors import ConfigError
    path = write_config(imap_config())
    secret = 'secret-app-password\r\nA1 STORE 1 +FLAGS (\\Seen)'
    with pytest.raises(ConfigError) as failure:
        TentaclesService.open(path, env={'MAIL_PASSWORD': secret}, imap_factory=FakeIMAP().factory)
    assert 'secret-app-password' not in str(failure.value)
    assert 'password' in str(failure.value)


def test_changed_account_resets_uid_cursor_and_scheduled_mail_fetch_uses_same_factory(write_config):
    from datetime import timedelta
    fake = FakeIMAP({'"INBOX"': Mailbox(messages={100: mail(message_id='<old-account>')})})
    path = write_config(imap_config())
    now = [NOW]

    async def scenario():
        service = TentaclesService.open(path, env={'MAIL_PASSWORD': 'secret-app-password'},
                                        imap_factory=fake.factory, clock=lambda: now[0])
        try:
            assert (await service.fetch_sources())[0].new == 1
        finally:
            await service.aclose()
        write_config(imap_config(host='different.example.test'))
        fake.folders['"INBOX"'].messages = {1: mail(message_id='<new-account>')}
        service = TentaclesService.open(path, env={'MAIL_PASSWORD': 'secret-app-password'},
                                        imap_factory=fake.factory, clock=lambda: now[0])
        try:
            assert (await service.fetch_sources())[0].new == 1
            assert [e for e in fake.events if e[0] == 'SEARCH'][-1][3] == 'SINCE'
            now[0] += timedelta(minutes=31)
            fake.folders['"INBOX"'].messages[2] = mail(message_id='<new-scheduled>')
            outcome = await service.tick()
            assert len(outcome) == 1 and outcome[0].kind == 'fetch' and outcome[0].new == 1
            assert service.list_items(kinds=['email']).total == 3
            assert 'secret-app-password' not in json_text(service.list_sources()[0])
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_unconfigured_disabled_mailbox_does_not_connect_and_manual_fetch_fails_safely(write_config):
    fake = FakeIMAP()
    path = write_config(imap_config(enabled=False, username='', password=''))

    async def scenario():
        service = TentaclesService.open(path, env={}, imap_factory=fake.factory, clock=lambda: NOW)
        try:
            assert await service.fetch_sources() == [] and await service.tick() == []
            assert fake.events == []
            [outcome] = await service.fetch_sources(['inbox'])
            assert outcome.status == 'error' and 'not configured' in outcome.error
            assert fake.events == []
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_blank_plain_alternative_falls_back_to_html(write_config):
    fake = FakeIMAP({'"INBOX"': Mailbox(messages={1: mail(body='  \n', html='<p>Useful HTML body</p>')})})
    path = write_config(imap_config())

    async def scenario():
        service = TentaclesService.open(path, env={'MAIL_PASSWORD': 'secret-app-password'},
                                        imap_factory=fake.factory, clock=lambda: NOW)
        try:
            assert (await service.fetch_sources())[0].new == 1
            item = service.get_item(service.list_items().items[0].id)
            assert item.content == item.summary == 'Useful HTML body'
        finally:
            await service.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize('fault', ['validity', 'search', 'uid', 'fetch'])
def test_bad_imap_responses_are_retryable_without_advancing_uid(write_config, fault):
    fake = FakeIMAP({'"INBOX"': Mailbox(messages={7: mail()})})
    original_uid, original_response = fake.uid, fake.response
    active = [True]

    def response(code):
        return (code, [None]) if active[0] and fault == 'validity' else original_response(code)

    def uid(command, *args):
        if active[0]:
            if command == 'SEARCH' and fault == 'search' or command == 'FETCH' and fault == 'fetch':
                return 'NO', [b'secret-app-password and private server response']
            if command == 'FETCH' and fault == 'uid':
                return 'OK', [(b'1 (UID 99 BODY[] {12}', mail()), b')']
        return original_uid(command, *args)

    fake.uid, fake.response = uid, response
    path = write_config(imap_config())

    async def scenario():
        service = TentaclesService.open(path, env={'MAIL_PASSWORD': 'secret-app-password'},
                                        imap_factory=fake.factory, clock=lambda: NOW)
        try:
            failed = (await service.fetch_sources())[0]
            assert failed.status == 'error' and service.list_items().total == 0
            assert 'secret-app-password' not in failed.error and 'private server response' not in failed.error
            active[0] = False
            assert (await service.fetch_sources())[0].new == 1
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_failed_logout_falls_back_to_socket_shutdown_without_losing_success(write_config):
    fake = FakeIMAP({'"INBOX"': Mailbox(messages={1: mail()})})

    def logout():
        raise OSError('private server response secret-app-password')

    fake.logout = logout
    path = write_config(imap_config())

    async def scenario():
        service = TentaclesService.open(path, env={'MAIL_PASSWORD': 'secret-app-password'},
                                        imap_factory=fake.factory, clock=lambda: NOW)
        try:
            outcome = (await service.fetch_sources())[0]
            assert outcome.status == 'ok' and outcome.new == 1 and outcome.error is None
            assert ('shutdown',) in fake.events
            assert (await service.fetch_sources())[0].new == 0
        finally:
            await service.aclose()

    asyncio.run(scenario())
