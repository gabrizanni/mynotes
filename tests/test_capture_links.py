"""Real connection path: URL resolution, safe failures and repair without duplication."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
import ssl
import urllib.error

import pytest

from mynotes import capture, capture_links as links, capture_dashboard
from mynotes.cli import main
from mynotes.utils import atomic_json_write
from test_capture import store, subscription, feed, component

GOOGLE_ID = 'calendar-unit@example.test'
EMBED = 'https://calendar.google.com/calendar/embed?src=calendar-unit%40example.test&ctz=Asia%2FKolkata'
ICAL = 'https://calendar.google.com/calendar/ical/calendar-unit%40example.test/public/basic.ics'
PRIVATE = 'https://calendar.google.com/calendar/ical/calendar-unit%40example.test/private-test-token/basic.ics'


def source(**changes):
    return subscription(importMode='all_timed', mappings=[], **changes)


@pytest.fixture(autouse=True)
def capable(monkeypatch):
    monkeypatch.setattr(capture, 'calendar_recordings_supported', lambda _: True)


@pytest.mark.parametrize('raw,expected', [
    (EMBED, ICAL), ('  ' + EMBED + '\n', ICAL),
    ('webcal://calendar.example.test/opaque?token=secret', 'https://calendar.example.test/opaque?token=secret'),
    (PRIVATE, PRIVATE), ('https://calendar.example.test:8443/opaque', 'https://calendar.example.test:8443/opaque'),
    ('https://calendar.google.com/calendar/embed?src=a%2Bb%40example.test', 'https://calendar.google.com/calendar/ical/a%2Bb%40example.test/public/basic.ics'),
])
def test_resolve_known_links(raw, expected):
    assert links.resolve(raw) == expected


@pytest.mark.parametrize('raw,code', [
    (EMBED + '&src=second', 'GOOGLE_MULTIPLE_CALENDARS'),
    (EMBED + '&src=', 'GOOGLE_MULTIPLE_CALENDARS'),
    ('https://calendar.google.com/calendar/embed?ctz=UTC', 'GOOGLE_LINK_UNSUPPORTED'),
    ('https://calendar.google.com/calendar/embed?src=', 'GOOGLE_LINK_UNSUPPORTED'),
    ('https://calendar.google.com/calendar/embed?src=a%2Fb', 'GOOGLE_LINK_UNSUPPORTED'),
    ('https://calendar.google.com/calendar/embed?src=a%0Ab', 'GOOGLE_LINK_UNSUPPORTED'),
    ('https://calendar.google.com/calendar/u/0?cid=secret', 'GOOGLE_LINK_UNSUPPORTED'),
    ('https://accounts.google.com/login', 'GOOGLE_LINK_UNSUPPORTED'),
    ('http://example.test/feed', 'INVALID_URL'),
    ('https://user:secret@example.test/feed', 'INVALID_URL'),
    ('https://example.test:bad/feed', 'INVALID_URL'),
])
def test_reject_links_with_safe_diagnostics(raw, code):
    with pytest.raises(links.CalendarLinkError) as error:
        links.resolve(raw)
    assert error.value.code == code
    assert raw not in str(error.value)


def test_do_not_rewrite_lookalike_host():
    value = 'https://calendar.google.com.example.test/calendar/embed?src=one'
    assert links.resolve(value) == value


def test_initial_failure_does_not_publish_source_or_schedule(store, monkeypatch):
    before = {p: p.read_bytes() for p in [store.config_path, store.schedule_path, store.directory / 'sync.json'] if p.exists()}
    monkeypatch.setattr(capture, 'download', lambda *_: (b'<!doctype html><html>private-token</html>', {}))
    result = capture.connect(store, source())
    assert result['code'] == 'WEBPAGE_NOT_CALENDAR' and not result['retryable']
    assert not store.config()['sources'] and not store.schedule()['events']
    assert all(p.read_bytes() == data for p, data in before.items())
    assert not (store.directory / 'sync.json').exists()
    assert 'private-token' not in json.dumps(result)
    monkeypatch.setattr(capture, 'download', lambda *_: (feed(component()), {'etag': 'one'}))
    assert capture.connect(store, source())['status'] == 'CONNECTED'
    assert len(store.config()['sources']) == len(store.schedule()['events']) == 1


def test_embed_connect_is_one_download_and_same_projection_as_direct_feed(store, monkeypatch):
    calls = []
    def download(s, validators):
        calls.append((s['url'], validators))
        return feed(component(title='New subject')), {'etag': 'one'}
    monkeypatch.setattr(capture, 'download', download)
    result = capture.connect(store, source(url=EMBED, timezone='Asia/Kolkata'))
    assert result['status'] == 'CONNECTED' and result['summary']['pending'] == 1
    assert calls == [(ICAL, {})]
    assert store.config()['sources'][0]['url'] == ICAL
    assert store.config()['sources'][0]['timezone'] == 'Asia/Kolkata'
    old = store.schedule()['events']
    assert capture.connect(store, source(url=ICAL, timezone='Asia/Kolkata'))['status'] == 'CONNECTED'
    assert store.schedule()['events'] == old
    assert not store.config()['enabled']


def test_empty_calendar_is_success_with_warning(store, monkeypatch):
    monkeypatch.setattr(capture, 'download', lambda *_: (feed(), {}))
    result = capture.connect(store, source())
    assert result['status'] == 'CONNECTED' and result['summary']['imported'] == 0
    assert 'non contiene lezioni' in result['message']


def test_native_capability_is_checked_before_download(store, monkeypatch):
    monkeypatch.setattr(capture, 'calendar_recordings_supported', lambda _: False)
    monkeypatch.setattr(capture, 'download', lambda *_: pytest.fail('must not download'))
    assert capture.connect(store, source())['code'] == 'RECORDER_INCOMPATIBLE'
    assert not store.config()['sources']


def test_legacy_embed_repair_preserves_identity_overrides_and_validators(store, monkeypatch):
    capture.subscribe(store, source(url=ICAL))
    config = store.config(); config['sources'][0]['url'] = EMBED; store.save_config(config)
    calls = []
    def download(s, meta):
        calls.append((s['url'], meta))
        return feed(component()), {'etag': 'new'}
    monkeypatch.setattr(capture, 'download', download)
    atomic_json_write(store.directory / 'sync.json', {'sources': {'teaching': {'error': 'ValueError', 'etag': 'old'}}})
    assert capture.sync(store)['status'] == 'SYNCED'
    assert calls == [(ICAL, {})]
    event = store.schedule()['events'][0]
    capture_dashboard.edit_lesson(store, event['id'], {'recordingEnabled': False})
    old = store.schedule()['events']
    assert capture.connect(store, source(url=PRIVATE))['status'] == 'CONNECTED'
    assert store.schedule()['events'] == old
    assert len(store.config()['sources']) == 1
    meta = capture.read_json(store.directory / 'sync.json')['sources']['teaching']
    assert meta['error'] is None and 'message' not in meta and meta['etag'] == 'new'
    assert store.schedule()['events'][0]['recordingID'] == event['recordingID']


def test_failed_source_can_be_corrected_but_populated_source_cannot_be_replaced(store, monkeypatch):
    capture.subscribe(store, source())
    monkeypatch.setattr(capture, 'download', lambda *_: (feed(component()), {}))
    assert capture.connect(store, source(url=ICAL))['status'] == 'CONNECTED'
    before = store.schedule_path.read_bytes()
    assert capture.connect(store, source(url='https://other.example.test/feed'))['code'] == 'SOURCE_CHANGED'
    assert store.schedule_path.read_bytes() == before


def test_connect_rereads_course_catalog_and_activation_after_network(store, monkeypatch):
    def download(*_):
        settings = store.workspace.configuration_store().load()
        settings['courses'][0]['aliases'].append('new subject')
        store.workspace.configuration_store().save(settings)
        config = store.config(); config['paused'] = True; store.save_config(config)
        return feed(component(title='New subject')), {}
    monkeypatch.setattr(capture, 'download', download)
    result = capture.connect(store, source())
    assert result['summary']['recognizedCourses'] == 1
    assert store.schedule()['events'][0]['courseCode'] == 'MATH'
    assert store.config()['paused'] and not store.config()['enabled']


@pytest.mark.parametrize('error,code,retryable', [
    (urllib.error.HTTPError('https://secret.test/token', 403, 'secret', {}, None), 'ACCESS_DENIED', False),
    (urllib.error.HTTPError('https://secret.test/token', 404, 'secret', {}, None), 'FEED_UNAVAILABLE', False),
    (urllib.error.HTTPError('https://secret.test/token', 503, 'secret', {}, None), 'NETWORK_ERROR', True),
    (urllib.error.URLError(TimeoutError('secret')), 'TIMEOUT', True),
    (urllib.error.URLError(ssl.SSLCertVerificationError('secret')), 'TLS_ERROR', False),
    (urllib.error.URLError('secret'), 'NETWORK_ERROR', True),
    (ValueError('secret'), 'INVALID_CALENDAR', False),
])
def test_safe_failure_classification(error, code, retryable):
    result = links.diagnostic(error)
    assert (result['code'], result['retryable']) == (code, retryable)
    assert 'secret' not in json.dumps(result)


def test_unavailable_google_public_feed_suggests_private_ical():
    error = urllib.error.HTTPError(ICAL, 404, 'provider', {}, None)
    assert links.diagnostic(error, ICAL)['suggestedAction'] == 'use_private_ical'
    with pytest.raises(links.CalendarLinkError, match='privato'):
        links.validate_body(b'<html>login</html>', ICAL)


def test_permanent_errors_suspend_automatic_retry_manual_retry_works(store, monkeypatch):
    capture.subscribe(store, source())
    monkeypatch.setattr(capture, 'download', lambda *_: (b'<html>web</html>', {'etag': 'bad'}))
    result = capture.sync(store)
    assert result['sources'][0]['code'] == 'WEBPAGE_NOT_CALENDAR'
    meta = capture.read_json(store.directory / 'sync.json')['sources']['teaching']
    assert meta['nextAttempt'] is None and meta['retryable'] is False
    monkeypatch.setattr(capture, 'download', lambda *_: pytest.fail('automatic retry must wait for correction'))
    assert capture.sync(store, if_due=True)['sources'] == []
    monkeypatch.setattr(capture, 'download', lambda *_: (feed(component()), {'etag': 'good'}))
    assert capture.sync(store)['status'] == 'SYNCED'
    meta = capture.read_json(store.directory / 'sync.json')['sources']['teaching']
    assert meta['error'] is None and 'retryable' not in meta


def test_transient_failure_keeps_last_schedule_and_validators_then_304(store, monkeypatch):
    capture.subscribe(store, source())
    monkeypatch.setattr(capture, 'download', lambda *_: (feed(component()), {'etag': 'good'}))
    capture.sync(store)
    old = deepcopy(store.schedule()['events'])
    def fail(*_):
        raise urllib.error.URLError(TimeoutError('private-token'))
    monkeypatch.setattr(capture, 'download', fail)
    now = datetime.now(UTC)
    assert capture.sync(store, now=now)['status'] == 'SYNC_FAILED'
    meta = capture.read_json(store.directory / 'sync.json')['sources']['teaching']
    assert meta['etag'] == 'good' and meta['retryable']
    assert capture.timestamp(meta['nextAttempt']) == capture.timestamp(capture.iso(now + timedelta(minutes=15)))
    assert store.schedule()['events'] == old
    monkeypatch.setattr(capture, 'download', lambda _, m: (None, m))
    assert capture.sync(store)['status'] == 'SYNCED'
    meta = capture.read_json(store.directory / 'sync.json')['sources']['teaching']
    assert meta['error'] is None and 'retryable' not in meta and meta['etag'] == 'good'
    assert store.schedule()['events'] == old


def test_cli_connect_and_private_source_lookup(store, monkeypatch, tmp_path, capsys):
    path = tmp_path / 'source.json'; path.write_text(json.dumps(source(url=EMBED)))
    monkeypatch.setattr(capture, 'download', lambda *_: (feed(component()), {}))
    assert main(['capture', 'connect', '--workspace', str(store.workspace.root), '--file', str(path), '--json']) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'CONNECTED'
    assert main(['capture', 'source', '--workspace', str(store.workspace.root), '--source', 'teaching', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['source']['url'] == ICAL
    dashboard = capture_dashboard.dashboard(store)
    assert ICAL not in json.dumps(dashboard)


class Response:
    def __init__(self, body, url='https://calendar.example.test/opaque'):
        self.body = body; self.url = url; self.headers = {'Content-Type': 'text/plain'}
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self, size): return self.body[:size]


def test_download_limits_https_redirects_and_mime_is_not_required(monkeypatch):
    class Opener:
        def open(self, request, timeout): return Response(feed(component()))
    handlers = []
    def opener(handler): handlers.append(handler); return Opener()
    monkeypatch.setattr(capture.urllib.request, 'build_opener', opener)
    data, _ = capture.download({'url': 'https://calendar.example.test/opaque'}, {})
    links.validate_body(data, 'https://calendar.example.test/opaque')
    with pytest.raises(links.CalendarLinkError) as error:
        handlers[0].redirect_request(None, None, 302, '', {}, 'http://unsafe.test/feed')
    assert error.value.code == 'INSECURE_REDIRECT'
    monkeypatch.setattr(Response, 'read', lambda *_: b'x' * (capture.MAX_FEED_BYTES + 1))
    with pytest.raises(links.CalendarLinkError) as error:
        capture.download({'url': 'https://calendar.example.test/opaque'}, {})
    assert error.value.code == 'FEED_TOO_LARGE'
