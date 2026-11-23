import hashlib
import io

import pytest

from mynotes import windows_speech


class Response(io.BytesIO):
    def __init__(self, data, *, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {}


def test_default_model_download_is_verified_before_publish(tmp_path, monkeypatch):
    download = b"synthetic pinned model"
    monkeypatch.setattr(windows_speech, "model_directory", lambda: tmp_path)
    monkeypatch.setattr(windows_speech, "MODEL_SIZE", len(download))
    monkeypatch.setattr(windows_speech, "MODEL_SHA256", hashlib.sha256(download).hexdigest())
    monkeypatch.setattr(windows_speech, "urlopen", lambda *a, **k: Response(b"x" * len(download)))
    with pytest.raises(ValueError, match="checksum"):
        windows_speech.install_default_model()
    assert not list(tmp_path.glob("ggml-*.bin"))
    assert not list(tmp_path.glob("*.download"))

    monkeypatch.setattr(windows_speech, "urlopen", lambda *a, **k: Response(download))
    model = windows_speech.install_default_model()
    assert model.read_bytes() == download
    model.write_bytes(b"existing local model")
    with pytest.raises(ValueError, match="Unexpected model"):
        windows_speech.install_default_model()
    assert model.read_bytes() == b"existing local model"


def test_default_model_download_resumes_verified_partial_bytes(tmp_path, monkeypatch):
    content = b"partial model"
    monkeypatch.setattr(windows_speech, "model_directory", lambda: tmp_path)
    monkeypatch.setattr(windows_speech, "MODEL_SIZE", len(content))
    monkeypatch.setattr(windows_speech, "MODEL_SHA256", hashlib.sha256(content).hexdigest())
    received = []

    def fetch(request, **_kwargs):
        requested_range = request.get_header("Range")
        received.append(requested_range)
        if not requested_range:
            return Response(content[:5])
        return Response(content[5:], status=206, headers={"Content-Range": f"bytes 5-{len(content)-1}/{len(content)}"})

    monkeypatch.setattr(windows_speech, "urlopen", fetch)
    assert windows_speech.install_default_model().read_bytes() == content
    assert received == [None, "bytes=5-"]
