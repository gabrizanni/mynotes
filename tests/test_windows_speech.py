import hashlib
import io

import pytest

from mynotes import windows_speech


def test_default_model_download_is_verified_before_publish(tmp_path, monkeypatch):
    download = b"synthetic pinned model"
    monkeypatch.setattr(windows_speech, "model_directory", lambda: tmp_path)
    monkeypatch.setattr(windows_speech, "MODEL_SIZE", len(download))
    monkeypatch.setattr(windows_speech, "MODEL_SHA256", hashlib.sha256(download).hexdigest())
    monkeypatch.setattr(windows_speech, "urlopen", lambda *a, **k: io.BytesIO(b"wrong content"))
    with pytest.raises(ValueError, match="checksum"):
        windows_speech.install_default_model()
    assert not list(tmp_path.glob("ggml-*.bin"))
    assert not list(tmp_path.glob("download-*"))

    monkeypatch.setattr(windows_speech, "urlopen", lambda *a, **k: io.BytesIO(download))
    model = windows_speech.install_default_model()
    assert model.read_bytes() == download
    model.write_bytes(b"existing local model")
    with pytest.raises(ValueError, match="Unexpected model"):
        windows_speech.install_default_model()
    assert model.read_bytes() == b"existing local model"
