import json
import sqlite3
from pathlib import Path

import pytest

from mynotes.publication import PublicationError, build_word_candidate, publish_word_candidate
from mynotes.utils import atomic_json_write, file_sha256
from mynotes.workflow import WorkflowState, generate_candidate
from test_publication import prepared, fake_render
from test_providers_quality import configured_workspace, source_pack, CandidateProvider


def snapshots(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}


def test_repeated_publish_is_idempotent_and_does_not_change_files(tmp_path, monkeypatch):
    workspace,target=prepared(tmp_path)
    monkeypatch.setattr('mynotes.publication._render_and_validate',fake_render)
    built=build_word_candidate(workspace,'OPS7-L01')
    assert publish_word_candidate(workspace,'OPS7-L01',Path(built['candidate']))['status']=='PUBLISHED'
    before=snapshots(tmp_path)
    monkeypatch.setattr('mynotes.publication._journal',lambda *_:pytest.fail('duplicate publish must not create a journal'))
    monkeypatch.setattr('mynotes.publication._archive',lambda *_args,**_kwargs:pytest.fail('duplicate publish must not create a backup'))
    assert publish_word_candidate(workspace,'OPS7-L01',Path(built['candidate']))['status']=='SKIPPED'
    assert snapshots(tmp_path)==before


@pytest.mark.parametrize('mutation',["missing","hash","path","status"])
def test_identical_word_without_matching_published_row_does_not_skip(tmp_path, monkeypatch, mutation):
    workspace,target=prepared(tmp_path)
    monkeypatch.setattr('mynotes.publication._render_and_validate',fake_render)
    built=build_word_candidate(workspace,'OPS7-L01')
    publish_word_candidate(workspace,'OPS7-L01',Path(built['candidate']))
    with sqlite3.connect(workspace.database_path) as connection:
        if mutation=='missing': connection.execute("DELETE FROM lesson_runs")
        elif mutation=='hash': connection.execute("UPDATE lesson_runs SET output_hash='wrong'")
        elif mutation=='path': connection.execute("UPDATE lesson_runs SET output_path=?",(str(tmp_path/'other.docx'),))
        else: connection.execute("UPDATE lesson_runs SET status='FAILED'")
    original=file_sha256(target)
    with pytest.raises(PublicationError):
        publish_word_candidate(workspace,'OPS7-L01',Path(built['candidate']))
    assert file_sha256(target)==original


def test_publish_metadata_mutation_keeps_generated_candidate_reusable(tmp_path, monkeypatch):
    workspace=configured_workspace(tmp_path);source_pack(workspace)
    monkeypatch.setattr('mynotes.publication._render_and_validate',fake_render)
    provider=CandidateProvider()
    assert generate_candidate(workspace,'OPS7-L01',provider_adapter=provider)['status']=='CANDIDATE_READY'
    built=build_word_candidate(workspace,'OPS7-L01')
    assert publish_word_candidate(workspace,'OPS7-L01',Path(built['candidate']))['status']=='PUBLISHED'
    source=workspace.runs_dir/'OPS7-L01/lecture_notes.candidate.json'
    with sqlite3.connect(workspace.database_path) as connection:
        row=connection.execute('SELECT candidate_hash,status FROM candidate_runs WHERE lecture_id=?',('OPS7-L01',)).fetchone()
    assert row==(file_sha256(source),'PUBLISHED')
    assert generate_candidate(workspace,'OPS7-L01',provider_adapter=provider)['status']=='SKIPPED'
    assert provider.calls==['writing']


@pytest.mark.parametrize('accept',[False,True])
def test_wrong_lecture_candidate_rejected_even_with_accept_current(tmp_path, monkeypatch, accept):
    workspace,target=prepared(tmp_path)
    monkeypatch.setattr('mynotes.publication._render_and_validate',fake_render)
    built=build_word_candidate(workspace,'OPS7-L01')
    with pytest.raises(PublicationError,match='different lecture'):
        publish_word_candidate(workspace,'OPS7-L02',Path(built['candidate']),accept_current_word=accept)
    assert not target.exists()


@pytest.mark.parametrize('accept',[False,True])
def test_course_target_changed_since_build_rejected_even_with_accept_current(tmp_path, monkeypatch, accept):
    workspace,target=prepared(tmp_path)
    monkeypatch.setattr('mynotes.publication._render_and_validate',fake_render)
    built=build_word_candidate(workspace,'OPS7-L01')
    settings=workspace.configuration_store().load()
    settings['courses'][0]['folders']['handout']=str(tmp_path/'other-course-folder')
    workspace.configuration_store().save(settings)
    with pytest.raises(PublicationError,match='different course target'):
        publish_word_candidate(workspace,'OPS7-L01',Path(built['candidate']),accept_current_word=accept)
    assert not target.exists() and not (tmp_path/'other-course-folder/OPS7_COURSE_NOTES.docx').exists()


@pytest.mark.parametrize('accept',[False,True])
def test_notes_edited_after_build_are_not_marked_published(tmp_path, monkeypatch, accept):
    workspace,target=prepared(tmp_path)
    monkeypatch.setattr('mynotes.publication._render_and_validate',fake_render)
    built=build_word_candidate(workspace,'OPS7-L01')
    source=workspace.runs_dir/'OPS7-L01/lecture_notes.candidate.json'
    payload=json.loads(source.read_text());payload['notes']['summary']='Edited after build.'
    atomic_json_write(source,payload)
    before=source.read_bytes()
    with pytest.raises(PublicationError,match='changed after Word build'):
        publish_word_candidate(workspace,'OPS7-L01',Path(built['candidate']),accept_current_word=accept)
    assert not target.exists() and source.read_bytes()==before
    assert not (workspace.state_dir/'publication-transactions').exists()


def test_sidecar_records_source_fingerprint_and_old_reports_require_rebuild(tmp_path, monkeypatch):
    workspace,target=prepared(tmp_path)
    monkeypatch.setattr('mynotes.publication._render_and_validate',fake_render)
    built=build_word_candidate(workspace,'OPS7-L01')
    source=workspace.runs_dir/'OPS7-L01/lecture_notes.candidate.json'
    assert built['source_candidate_hash']==file_sha256(source)
    report=Path(built['candidate']).with_suffix('.validation.json')
    metadata=json.loads(report.read_text());metadata.pop('source_candidate_hash');atomic_json_write(report,metadata)
    with pytest.raises(PublicationError,match='no notes fingerprint'):
        publish_word_candidate(workspace,'OPS7-L01',Path(built['candidate']))
    assert not target.exists()
