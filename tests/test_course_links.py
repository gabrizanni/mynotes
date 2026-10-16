import json
from pathlib import Path

import pytest

from mynotes.cli import main
from mynotes.configuration import course_template
from mynotes.course_links import inspect_course_folder, link_course_folder
from mynotes.workspace import Workspace


def configured(tmp_path):
    workspace = Workspace.from_value(tmp_path / 'private')
    workspace.initialize()
    settings = workspace.configuration_store().load()
    course = course_template()
    course.update(code='HIST', name='Economic History')
    settings['courses'] = [course]
    settings['paths']['recording_dirs'] = [str(tmp_path / 'other course audio')]
    workspace.configuration_store().save(settings)
    return workspace


def make_sources(tmp_path):
    root = tmp_path / 'Economic History'
    root.mkdir()
    (root / 'slides L2.pdf').write_bytes(b'not parsed during linking')
    notes = root / 'Appunti'; notes.mkdir()
    (notes / 'notes.docx').write_bytes(b'not parsed during linking')
    audio = root / 'Registrazioni'; audio.mkdir()
    (audio / 'storia 2.mp3').write_bytes(b'not transcribed during linking')
    return root, notes, audio


def test_single_cli_call_links_roles_without_reading_sources(tmp_path, monkeypatch, capsys):
    workspace = configured(tmp_path)
    root, notes, audio = make_sources(tmp_path)
    original = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    real_read = Path.read_bytes
    def protect(path):
        if path in original:
            pytest.fail('linking must not read source contents')
        return real_read(path)
    monkeypatch.setattr(Path, 'read_bytes', protect)
    assert main(['course', 'link', 'HIST', str(root), '--workspace', str(workspace.root), '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'LINKED'
    assert report['connections'] == {'materials': str(root), 'personal_notes': str(notes), 'recordings': str(audio)}
    saved = workspace.configuration_store().load()
    assert saved['courses'][0]['folders']['personal_notes'] == str(notes)
    assert saved['paths']['recording_dirs'] == [str(tmp_path / 'other course audio'), str(audio)]
    assert report['inventory']['files']['materials'] == [str(root / 'slides L2.pdf')]
    for source, content in original.items():
        assert real_read(source) == content


def test_ambiguous_audio_needs_selection_and_keeps_configuration(tmp_path):
    workspace = configured(tmp_path); root, notes, audio = make_sources(tmp_path)
    other = root / 'Audio'; other.mkdir(); (other / 'lecture.mp3').write_bytes(b'audio')
    before = workspace.configuration_path.read_bytes()
    result = link_course_folder(workspace, 'HIST', root)
    assert result['status'] == 'NEEDS_SELECTION' and result['saved'] is False
    assert workspace.configuration_path.read_bytes() == before
    result = link_course_folder(workspace, 'HIST', root, recordings=audio)
    assert result['status'] == 'LINKED'
    assert result['connections']['recordings'] == str(audio)


def test_dry_run_repeat_and_other_courses_preserved(tmp_path):
    workspace = configured(tmp_path); root, notes, audio = make_sources(tmp_path)
    before = workspace.configuration_path.read_bytes()
    report = link_course_folder(workspace, 'HIST', root, dry_run=True)
    assert report['status'] == 'READY' and workspace.configuration_path.read_bytes() == before
    link_course_folder(workspace, 'HIST', root)
    after = workspace.configuration_path.read_bytes()
    snapshots = sorted(workspace.configuration_history_dir.glob('*.json'))
    link_course_folder(workspace, 'HIST', root)
    assert workspace.configuration_path.read_bytes() == after
    assert sorted(workspace.configuration_history_dir.glob('*.json')) == snapshots


def test_inventory_is_bounded_and_does_not_recurse_deeply(tmp_path):
    root = tmp_path / 'course'; root.mkdir()
    deeper = root / 'topic' / 'deeper'; deeper.mkdir(parents=True)
    (deeper / 'hidden slide.pdf').write_bytes(b'ignored')
    for number in range(6): (root / f'{number}.pdf').write_bytes(b'ignored')
    report = inspect_course_folder(root, max_entries=3)
    assert report['entries_scanned'] == 3 and report['inventory_truncated'] is True
    assert str(deeper / 'hidden slide.pdf') not in report['files']['materials']


def test_read_only_link_on_missing_workspace_does_not_initialize(tmp_path, capsys):
    root = tmp_path / 'missing'; folder = tmp_path / 'folder'; folder.mkdir()
    assert main(['course', 'link', 'HIST', str(folder), '--workspace', str(root), '--dry-run']) == 2
    assert not root.exists()


def test_named_recording_symlink_is_resolved_and_unrelated_link_not_scanned(tmp_path):
    root = tmp_path / 'course'; root.mkdir()
    audio = tmp_path / 'course audio'; audio.mkdir(); (audio / 'lecture.mp3').write_bytes(b'ignored')
    unrelated = tmp_path / 'unrelated'; unrelated.mkdir(); (unrelated / 'private.txt').write_bytes(b'ignored')
    (root / 'Registrazioni').symlink_to(audio, target_is_directory=True)
    (root / 'unrelated').symlink_to(unrelated, target_is_directory=True)
    report = inspect_course_folder(root)
    assert report['role_candidates']['recordings'] == [str(audio)]
    assert not report['files']['personal_notes']


@pytest.mark.skipif(__import__('sys').platform != 'darwin', reason='Finder alias requires macOS')
def test_first_course_accepts_real_finder_aliases_without_changing_sources(tmp_path, monkeypatch, capsys):
    from test_path_links import _create_alias
    root, notes, audio = make_sources(tmp_path)
    materials_alias = tmp_path / 'course alias'
    audio_alias = tmp_path / 'audio alias'
    _create_alias(root, materials_alias); _create_alias(audio, audio_alias)
    before = (materials_alias.read_bytes(), audio_alias.read_bytes())
    monkeypatch.setenv('MYNOTES_DOCUMENTS_ROOT', str(tmp_path / 'Documents'))
    monkeypatch.setenv('MYNOTES_USER_HOME', str(tmp_path / 'user'))
    monkeypatch.setenv('MYNOTES_HOME', str(tmp_path / 'managed'))
    workspace = tmp_path / 'private'
    assert main(['course', 'add', '--name', 'Economic History', '--notes-language', 'it',
                 '--materials', str(materials_alias), '--recordings', str(audio_alias),
                 '--workspace', str(workspace)]) == 0
    course = json.loads(capsys.readouterr().out)
    assert Path(course['folders']['materials']).samefile(root)
    stored = Workspace.from_value(workspace).configuration_store().load()
    assert any(Path(folder).samefile(audio) for folder in stored['paths']['recording_dirs'])
    assert (materials_alias.read_bytes(), audio_alias.read_bytes()) == before


def test_large_inventory_reports_counts_and_caps_agent_output(tmp_path):
    root = tmp_path / 'large course'; root.mkdir()
    for number in range(55):
        (root / f'slides-{number:03d}.pdf').write_bytes(b'not read')
    report = inspect_course_folder(root)
    assert report['file_counts']['materials'] == 55
    assert len(report['files']['materials']) == 50
    assert report['file_list_truncated'] == ['materials']
    assert report['inventory_truncated'] is False


def test_same_directory_role_shortcuts_do_not_duplicate_inventory(tmp_path):
    root = tmp_path / 'course'; root.mkdir()
    (root / 'lecture.mp3').write_bytes(b'not read')
    (root / 'Registrazioni').symlink_to(root, target_is_directory=True)
    report = inspect_course_folder(root)
    assert report['files']['recordings'] == [str(root / 'lecture.mp3')]
    assert report['file_counts']['recordings'] == 1
