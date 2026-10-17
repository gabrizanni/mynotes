"""External text/documents remain material; they never start ASR or gain timing."""
from pathlib import Path
import json

from docx import Document
import pytest

from mynotes.cli import main
from test_providers_quality import configured_workspace


@pytest.mark.parametrize('extension',['txt','md','docx'])
def test_existing_material_preparation_does_not_transcribe_text(tmp_path,monkeypatch,capsys,extension):
    workspace=configured_workspace(tmp_path)
    source=tmp_path/('external.'+extension)
    content='An externally supplied conversation explains the inventory trade-off. Its source contains no audio timing.'
    if extension=='docx':
        document=Document();document.add_paragraph(content);document.save(source)
    else:source.write_text(content)
    monkeypatch.setattr('mynotes.asr.transcribe_audio',lambda *_a,**_k:pytest.fail('Text materials must never start ASR'))
    assert main(['prepare','OPS7-L01','--workspace',str(workspace.root),'--material',str(source)])==0
    pack=json.loads((workspace.runs_dir/'OPS7-L01/source_pack.json').read_text())
    assert any('inventory trade-off' in e.get('content',e.get('text','')) for e in pack['evidence'])
    assert all(e.get('kind')!='transcript' for e in pack['evidence'])
    assert not (workspace.runs_dir/'OPS7-L01/asr/transcript.json').exists()
