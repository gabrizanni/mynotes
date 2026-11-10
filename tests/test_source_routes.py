from pathlib import Path
from unittest.mock import patch

from mynotes.cli import main
from mynotes.context import task_context
from mynotes.onboarding import add_first_course
from mynotes.source_routes import remember_transcript_folder, route_index
from mynotes.workspace import Workspace


def test_creation_relink_and_export_refresh_search_locations(tmp_path, monkeypatch):
    monkeypatch.setenv('MYNOTES_USER_HOME', str(tmp_path / 'user'))
    monkeypatch.setenv('MYNOTES_HOME', str(tmp_path / 'runtime'))
    ws = Workspace(tmp_path / 'workspace'); ws.initialize()
    material = tmp_path / 'materials'; material.mkdir()
    audio = tmp_path / 'audio'; audio.mkdir()
    course = add_first_course(ws, 'Statistics', materials=str(material), recordings=str(audio))
    def routes():
        return route_index(ws, ws.configuration_store().load(), course['code'])['courses'][0]
    assert routes()['recordings'] == str(audio)
    assert routes()['materials'] == str(material)
    replacement = tmp_path / 'new audio'; replacement.mkdir()
    add_first_course(ws, 'Statistics', recordings=str(replacement))
    assert routes()['recordings'] == str(replacement)
    transcript = tmp_path / 'exports' / 'Lesson.txt'
    from mynotes.memory import writing_configuration
    writing_before = writing_configuration(ws.configuration_store().load(), course['code'])
    remember_transcript_folder(ws, {'course_code': course['code'], 'path': str(transcript)})
    assert str(transcript.parent) in routes()['transcripts']
    assert writing_configuration(ws.configuration_store().load(), course['code']) == writing_before
    before = ws.configuration_path.read_bytes()
    remember_transcript_folder(ws, {'course_code': course['code'], 'path': str(transcript)})
    assert ws.configuration_path.read_bytes() == before
    with patch.object(Path, 'iterdir', side_effect=AssertionError('No scan')), patch.object(Path, 'stat', side_effect=AssertionError('No stat')):
        context = task_context(ws.configuration_store().load(), 'notes', course['code'], workspace=ws)
    assert context['source_paths']['courses'][0]['recordings'] == str(replacement)


def test_folder_update_is_visible_without_reindexing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('MYNOTES_USER_HOME', str(tmp_path / 'user'))
    ws = Workspace(tmp_path / 'workspace'); ws.initialize()
    course = add_first_course(ws, 'Methods')
    update = tmp_path / 'patch.json'
    update.write_text('{"folders":{"recordings":"new-audio","transcripts":"new-transcripts"}}')
    assert main(['course', 'edit', course['code'], '--workspace', str(ws.root), '--file', str(update)]) == 0
    capsys.readouterr()
    assert main(['context', '--activity', 'course', '--course', course['code'], '--workspace', str(ws.root), '--json']) == 0
    import json
    routes = json.loads(capsys.readouterr().out)['source_paths']['courses'][0]
    assert routes['recordings'].endswith('/new-audio')
    assert routes['transcripts'][0].endswith('/new-transcripts')
