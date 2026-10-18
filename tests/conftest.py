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
