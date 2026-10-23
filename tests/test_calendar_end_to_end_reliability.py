"""Python-produced schedules consumed by the actual native scheduling model."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from mynotes import capture
from mynotes.utils import atomic_json_write
from test_capture import store, event, subscription, feed, component
from test_capture_calendar_all import capable, source


pytestmark = pytest.mark.skipif(sys.platform != 'darwin' or not shutil.which('swiftc'), reason='macOS Swift compiler required')


@pytest.fixture(scope='module')
def native_schedule(tmp_path_factory):
    root = tmp_path_factory.mktemp('calendar-native-boundary')
    main = root / 'main.swift'
    main.write_text('''import Foundation
let schedule = try CaptureJSON.load(Schedule.self, from: URL(fileURLWithPath: CommandLine.arguments[1]))
let checked = schedule.recordingEvents(["MATH"])
let now = CaptureJSON.decoder
let instant = try now.decode(Date.self, from: Data(("\\\"" + CommandLine.arguments[2] + "\\\"").utf8))
let result: [String: Any] = ["current": checked.events.filter { $0.isCurrent(instant) }.map(\\.id),
    "scheduled": checked.events.filter { $0.eligibility == "SCHEDULED" }.map(\\.id), "issues": checked.issues]
print(String(data: try JSONSerialization.data(withJSONObject: result), encoding: .utf8)!)
''')
    binary = root / 'schedule-model'
    model = Path(__file__).parents[1] / 'capture/Sources/MyNotesCapture/Models.swift'
    subprocess.run(['swiftc', '-module-cache-path', str(root / 'module-cache'), str(model), str(main), '-o', str(binary)], check=True, capture_output=True, text=True)
    def run(schedule, instant='2026-10-12T10:30:00Z'):
        path = root / 'schedule.json'
        path.write_text(json.dumps(schedule))
        result = subprocess.run([str(binary), str(path), instant], check=True, capture_output=True, text=True)
        return json.loads(result.stdout)
    return run


def test_legacy_association_repair_reaches_native_scheduler(store, monkeypatch, native_schedule):
    capable(store, monkeypatch)
    capture.subscribe(store, source())
    old = capture.normalize_event({**event(13), 'id': 'teaching:one:single', 'title': 'MATH 13'}, {'MATH'}, 'teaching')
    old.update(courseCode='OBSOLETE', eligibility='NEEDS_REVIEW', reason='Calendar event has invalid times')
    atomic_json_write(store.directory / 'source-events.json', {'schemaVersion': 1, 'events': [old]})
    atomic_json_write(store.directory / 'associations.json', {'schemaVersion': 1, 'recordings': {old['id']: {'courseCode': 'MATH', 'lessonNumber': 13, 'associationState': 'resolved'}}})
    monkeypatch.setattr(capture, 'download', lambda *_: (feed(component(title='MATH 13')), {}))
    for _ in range(2):
        assert capture.sync(store)['status'] == 'SYNCED'
        schedule = store.schedule()
        assert 'recordingID' not in schedule['events'][0]
        assert native_schedule(schedule)['current'] == [old['id']]


def test_adjacent_lessons_switch_at_end_without_postroll_admission(store, native_schedule):
    capture.import_schedule(store, [event(), event(2, '2026-10-12T11:00:00Z', '2026-10-12T12:00:00Z')])
    assert native_schedule(store.schedule(), '2026-10-12T10:59:59Z')['current'] == ['static:MATH-L01']
    assert native_schedule(store.schedule(), '2026-10-12T11:00:00Z')['current'] == ['static:MATH-L02']
    assert native_schedule(store.schedule(), '2026-10-12T12:00:30Z')['current'] == []


def test_invalid_calendar_occurrence_does_not_disable_valid_native_admission(store, monkeypatch, native_schedule):
    capable(store, monkeypatch)
    capture.subscribe(store, source())
    monkeypatch.setattr(capture, 'download', lambda *_: (feed(component(title='MATH 1')), {}))
    capture.sync(store)
    broken = component(title='MATH 1', end='20261012T090000Z')
    good = component(uid='good', title='MATH 2', start='20261012T100000Z', end='20261012T110000Z')
    monkeypatch.setattr(capture, 'download', lambda *_: (feed(broken, good), {}))
    capture.sync(store)
    assert native_schedule(store.schedule())['current'] == ['teaching:good:single']


def test_offline_refresh_retains_native_schedule_and_local_exclusion(store, monkeypatch, native_schedule):
    from mynotes import capture_dashboard
    capture.subscribe(store, subscription())
    monkeypatch.setattr(capture, 'download', lambda *_: (feed(component()), {}))
    capture.sync(store)
    identity = store.schedule()['events'][0]['id']
    capture_dashboard.edit_lesson(store, identity, {'recordingEnabled': False})
    def offline(*_):
        raise OSError('fixture offline')
    monkeypatch.setattr(capture, 'download', offline)
    assert capture.sync(store)['status'] == 'SYNC_FAILED'
    assert native_schedule(store.schedule())['scheduled'] == []
