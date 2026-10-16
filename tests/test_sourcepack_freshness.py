import pytest

from mynotes.utils import atomic_json_write
from mynotes.workflow import WorkflowError, generate_candidate
from test_providers_quality import configured_workspace, source_pack, CandidateProvider


@pytest.mark.parametrize("old", [None, {"cache_key":"old-transcription"}])
def test_changed_transcription_blocks_generation_before_provider(tmp_path, old):
    import json
    workspace = configured_workspace(tmp_path)
    path = source_pack(workspace)
    pack = json.loads(path.read_text())
    pack['transcription'] = old
    atomic_json_write(path,pack)
    atomic_json_write(path.parent/'source_pack-stale.json', {'transcription_cache_key':'new-transcription'})
    provider = CandidateProvider()
    with pytest.raises(WorkflowError,match='prepare again'):
        generate_candidate(workspace,'OPS7-L01',provider_adapter=provider)
    assert not provider.calls


def test_corrupt_freshness_checkpoint_blocks_generation(tmp_path):
    workspace = configured_workspace(tmp_path)
    path = source_pack(workspace)
    (path.parent/'source_pack-stale.json').write_text('{incomplete')
    provider = CandidateProvider()
    with pytest.raises(WorkflowError,match='freshness'):
        generate_candidate(workspace,'OPS7-L01',provider_adapter=provider)
    assert not provider.calls
