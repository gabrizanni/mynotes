import json
import subprocess
from pathlib import Path
from mynotes.providers import CodexAdapter, ProviderRequest, RequestedModel, _codex_schema, _normalize_transport_notes, notes_schema


def test_codex_strict_objects_require_all_keys_without_changing_portable_contract():
    original = notes_schema()
    strict = _codex_schema(original)
    def verify(value):
        if isinstance(value, dict):
            if 'properties' in value:
                assert set(value['required']) == set(value['properties'])
                assert value['additionalProperties'] is False
            for item in value.values():
                verify(item)
        elif isinstance(value, list):
            for item in value:
                verify(item)
    verify(strict)
    assert original['properties']['blocks']['items']['required'] == ['kind', 'evidence_ids']
    answer = strict['properties']['blocks']['items']['properties']['answer']
    assert answer['anyOf'] == [{'type': 'string'}, {'type': 'null'}]


def test_nullable_optional_transport_fields_restore_portable_notes():
    notes = {'formula': None, 'blocks': [{'kind': 'paragraph', 'text': 'A concept.', 'evidence_ids': ['T001'], 'answer': None, 'level': None, 'items': None}],
             'visual_decisions': [{'evidence_id': 'S001', 'decision': 'exclude', 'reason': 'No image needed.', 'inspected': None}]}
    normalized = _normalize_transport_notes(notes)
    assert normalized['formula'] is None
    assert normalized['blocks'][0] == {'kind': 'paragraph', 'text': 'A concept.', 'evidence_ids': ['T001']}
    assert 'inspected' not in normalized['visual_decisions'][0]
    assert notes['blocks'][0]['answer'] is None


def test_codex_adapter_passes_strict_schema_and_normalizes_result(tmp_path, monkeypatch):
    monkeypatch.setattr('mynotes.providers.shutil.which', lambda _: '/bin/codex')
    def run(command, **kwargs):
        schema = json.loads(Path(command[command.index('--output-schema') + 1]).read_text())
        assert set(schema['properties']['blocks']['items']['required']) == set(schema['properties']['blocks']['items']['properties'])
        Path(command[command.index('--output-last-message') + 1]).write_text(json.dumps({'formula': None, 'blocks': [{'kind': 'paragraph', 'text': 'Concept.', 'answer': None, 'evidence_ids': ['T001']}]}))
        return subprocess.CompletedProcess(command, 0, '{"type":"turn.completed"}\n', '')
    monkeypatch.setattr('mynotes.providers.subprocess.run', run)
    request = ProviderRequest('OPS-L01', {'name': 'Ops'}, {}, RequestedModel('codex', '', '', 'standard'), run_dir=tmp_path)
    notes = CodexAdapter().generate(request).notes
    assert notes['formula'] is None
    assert 'answer' not in notes['blocks'][0]
