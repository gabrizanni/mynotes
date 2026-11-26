import json
import pytest

@pytest.fixture
def ready_argmax():
    def create(engine, model):
        path=engine.model_dir / ('openai_whisper-'+model)
        for name in ('AudioEncoder','TextDecoder','MelSpectrogram'):
            d=path/(name+'.mlmodelc');d.mkdir(parents=True,exist_ok=True);(d/'model.bin').write_bytes(b'fixture compiled model')
        for name in ('tokenizer.json','tokenizer_config.json','vocab.json'):
            (path/name).write_text(json.dumps({'fixture':True}))
        (path/'merges.txt').write_text('# fixture\na b\n')
        return path
    return create


@pytest.fixture(autouse=True)
def reference_transcription_defaults(request, monkeypatch):
    """Pipeline fixtures use the macOS reference engines on every host.

    Linux course defaults (whisper.cpp) are covered by tests marked
    ``linux_defaults``, which opt out of this pin.
    """
    if request.node.get_closest_marker("linux_defaults") is None:
        from mynotes import configuration
        monkeypatch.setattr(configuration, "is_linux", lambda: False)
