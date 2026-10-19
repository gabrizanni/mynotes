"""Local fault tests for setup; no real network, auth or microphone implied."""
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location("setup_runtime", Path(__file__).parents[1] / "scripts/setup_runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def artifact(payload=b"payload"):
    return {"url": "https://example.test/component", "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)}


class Response:
    def __init__(self, payload, status=200, headers=None, failure=False):
        self.payload = payload
        self.status = status
        self.headers = headers or {}
        self.failure = failure
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def geturl(self): return "https://example.test/component"
    def read(self, _):
        if self.failure and not self.payload:
            raise OSError("disconnected")
        value, self.payload = self.payload, b""
        return value


def test_resume_interrupted_file_and_validate_range(tmp_path, monkeypatch):
    destination = tmp_path / "model"
    payload = b"firstsecond"
    responses = iter([Response(b"first", failure=True), Response(b"second", 206, {"Content-Range": "bytes 5-10/11"})])
    requests = []
    def open_url(request, **kwargs):
        requests.append(request)
        return next(responses)
    monkeypatch.setattr(runtime.urllib.request, "urlopen", open_url)
    with pytest.raises(OSError, match="disconnected"):
        runtime.download(artifact(payload), destination)
    assert destination.with_name("model.part").read_bytes() == b"first"
    assert runtime.download(artifact(payload), destination).read_bytes() == payload
    assert requests[1].get_header("Range") == "bytes=5-"
    assert not destination.with_name("model.part").exists()


def test_server_ignores_range_restart_not_append(tmp_path, monkeypatch):
    destination = tmp_path / "model"
    destination.with_name("model.part").write_bytes(b"bad")
    runtime.atomic_json(destination.with_name("model.part.json"), artifact())
    monkeypatch.setattr(runtime.urllib.request, "urlopen", lambda *a, **k: Response(b"payload"))
    assert runtime.download(artifact(), destination).read_bytes() == b"payload"


def test_bad_resume_range_does_not_append(tmp_path, monkeypatch):
    destination = tmp_path / "model"
    destination.with_name("model.part").write_bytes(b"pay")
    runtime.atomic_json(destination.with_name("model.part.json"), artifact())
    monkeypatch.setattr(runtime.urllib.request, "urlopen", lambda *a, **k: Response(b"load", 206, {"Content-Range": "bytes 2-6/7"}))
    with pytest.raises(runtime.SetupError, match="byte range"):
        runtime.download(artifact(), destination)
    assert destination.with_name("model.part").read_bytes() == b"pay"


def test_checksum_failed_partial_discarded_and_original_preserved(tmp_path, monkeypatch):
    destination = tmp_path / "model"
    destination.write_bytes(b"original")
    monkeypatch.setattr(runtime.urllib.request, "urlopen", lambda *a, **k: Response(b"corrupt"))
    with pytest.raises(runtime.SetupError, match="checksum"):
        runtime.download(artifact(), destination)
    assert destination.read_bytes() == b"original"
    assert not destination.with_name("model.part").exists()


def test_cached_verified_component_needs_no_network(tmp_path, monkeypatch):
    destination = tmp_path / "model"
    destination.write_bytes(b"payload")
    monkeypatch.setattr(runtime.urllib.request, "urlopen", Mock(side_effect=AssertionError("network")))
    runtime.download(artifact(), destination)


def test_space_failure_prevents_partial_write(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.shutil, "disk_usage", lambda _: Mock(free=0))
    monkeypatch.setattr(runtime.urllib.request, "urlopen", lambda *a, **k: Response(b"payload"))
    with pytest.raises(runtime.SetupError, match="free space"):
        runtime.download(artifact(), tmp_path / "model")
    assert not (tmp_path / "model.part").exists()


@pytest.mark.parametrize("value", ["../escape", "/etc/passwd", "."])
def test_component_paths_cannot_escape(tmp_path, value):
    with pytest.raises(runtime.SetupError, match="Unsafe"):
        runtime.safe_relative(tmp_path, value)


def test_shell_block_repeat_preserves_other_settings(tmp_path):
    original = "# my settings\nexport EDITOR=vim\n"
    (tmp_path / ".zshrc").write_text(original)
    runtime.ensure_shell_path(tmp_path, tmp_path / "local space")
    once = (tmp_path / ".zshrc").read_text()
    runtime.ensure_shell_path(tmp_path, tmp_path / "local space")
    assert (tmp_path / ".zshrc").read_text() == once
    assert once.startswith(original)
    assert once.count("# >>> MyNotes") == 1


def test_ambiguous_shell_block_preserved(tmp_path):
    original = "# >>> MyNotes managed PATH >>>\nno end\n"
    (tmp_path / ".zshrc").write_text(original)
    with pytest.raises(runtime.SetupError, match="Ambiguous"):
        runtime.ensure_shell_path(tmp_path, tmp_path / ".local")
    assert (tmp_path / ".zshrc").read_text() == original


def test_dmg_always_unmounted_on_copy_failure(tmp_path, monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if command[0] == "hdiutil" and command[1] == "attach":
            (Path(command[5]) / "LibreOffice.app").mkdir()
        if command[0] == "ditto":
            raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(runtime.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        runtime.install_dmg(tmp_path / "lo.dmg", tmp_path / "tools", "LibreOffice.app")
    assert calls[-1][:2] == ["hdiutil", "detach"]


def fake_release(tmp_path, monkeypatch):
    release = tmp_path / "release"
    (release / "dist").mkdir(parents=True)
    (release / "dist/mynotes-test.whl").write_bytes(b"new wheel")
    runtime.atomic_json(release / "setup-components.json", {"schema_version": 1, "components": []})
    runtime.atomic_json(release / "release-manifest.json", {"files": {}})
    (release / "pyproject.toml").write_text('[project]\nversion="0.2.1"\n')
    home = tmp_path / "runtime"
    monkeypatch.setenv("MYNOTES_HOME", str(home))
    monkeypatch.setenv("MYNOTES_APP_HOME", str(home / "app"))
    monkeypatch.setenv("MYNOTES_PREFIX", str(tmp_path / "prefix"))
    monkeypatch.setenv("MYNOTES_INSTALL_CAPTURE_GUARD", "1")
    monkeypatch.setenv("MYNOTES_USER_HOME", str(tmp_path / "user"))
    return release, home


def test_conflicting_same_version_preserved_before_downloads(tmp_path, monkeypatch):
    release, home = fake_release(tmp_path, monkeypatch)
    version = home / "app/versions/0.2.1"
    version.mkdir(parents=True)
    (version / "installation.json").write_text('{"wheel_sha256":"other artifact"}')
    (version / "keep").write_text("existing install")
    monkeypatch.setattr(runtime, "install_components", Mock(side_effect=AssertionError("must check version first")))
    assert runtime.main(["--release-root", str(release), "--provider", "codex"]) == 2
    assert (version / "keep").read_text() == "existing install"
    assert "different artifact" in json.loads((home / "setup-progress.json").read_text())["error"]


def test_fresh_smoke_failure_removes_selection_keeps_retryable_version(tmp_path, monkeypatch):
    release, home = fake_release(tmp_path, monkeypatch)
    launcher = tmp_path / "prefix/bin/mynotes"
    monkeypatch.setattr(runtime, "install_components", lambda *args: {"schema_version": 1})
    def run(command, **kwargs):
        if command[0] == "/bin/zsh":
            version = home / "app/versions/0.2.1"
            (version / "venv/bin").mkdir(parents=True)
            (version / "venv/bin/python").write_text("fake")
            (version / "venv/bin/mynotes").write_text("fake")
            (home / "app/current").symlink_to(version)
            launcher.parent.mkdir(parents=True)
            launcher.write_text("managed")
        elif "verify_local_components" in command[2]:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(runtime.subprocess, "run", run)
    assert runtime.main(["--release-root", str(release), "--provider", "codex"]) == 2
    assert not (home / "app/current").exists()
    assert not launcher.exists()
    assert not (home / "tools.json").exists()
    assert (home / "app/versions/0.2.1").exists()


@pytest.mark.parametrize("interrupt", [None, "sigint", "sigterm"])
def test_update_smoke_failure_restores_previous_selection_registry(tmp_path, monkeypatch, interrupt):
    release, home = fake_release(tmp_path, monkeypatch)
    old = home / "app/versions/0.1.1"
    old.mkdir(parents=True)
    (home / "app/current").symlink_to(old)
    old_tools = {"argmax": "/old/argmax", "soffice": "/old/soffice"}
    runtime.atomic_json(home / "tools.json", old_tools)
    runtime.atomic_json(home / "setup.json", {"version": "0.1.1"})
    monkeypatch.setattr(runtime, "install_components", lambda *args: {"argmax": "/new/argmax"})
    def run(command, **kwargs):
        if command[0] == "/bin/zsh":
            version = home / "app/versions/0.2.1"
            version.mkdir(parents=True)
            (home / "app/current").unlink()
            (home / "app/current").symlink_to(version)
        elif "verify_local_components" in command[2]:
            if interrupt == "sigint":
                raise KeyboardInterrupt
            if interrupt == "sigterm":
                import signal
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(runtime.subprocess, "run", run)
    assert runtime.main(["--release-root", str(release), "--provider", "codex"]) == 2
    assert (home / "app/current").resolve() == old.resolve()
    assert json.loads((home / "tools.json").read_text()) == old_tools
    assert json.loads((home / "setup.json").read_text()) == {"version": "0.1.1"}


def test_retry_after_failed_smoke_uses_rollback_and_restores_launcher(tmp_path, monkeypatch):
    """Exercise the real rollback shell path; dependencies/smoke are synthetic."""
    import os
    import sys
    release, home = fake_release(tmp_path, monkeypatch)
    scripts = release / "scripts"
    scripts.mkdir()
    source = Path(__file__).parents[1] / "scripts"
    for name in ("install-mynotes.sh", "capture_install_guard.py"):
        runtime.shutil.copy2(source / name, scripts / name)
    monkeypatch.setenv("MYNOTES_CAPTURE_HOME", str(home / "Capture"))
    launcher = tmp_path / "prefix/bin/mynotes"
    monkeypatch.setattr(runtime, "install_components", lambda *args: {"schema_version": 1})
    real_run = subprocess.run
    smoke_attempts = []
    shell_calls = []
    def run(command, **kwargs):
        if command[0] == "/bin/zsh":
            shell_calls.append(command)
            if "--rollback" in command:
                return real_run(command, **kwargs)
            version = home / "app/versions/0.2.1"
            (version / "venv/bin").mkdir(parents=True)
            (version / "venv/bin/python").symlink_to(sys.executable)
            executable = version / "venv/bin/mynotes"
            executable.write_text("#!/bin/sh\nprintf 'fixture launcher works\\n'\n")
            executable.chmod(0o755)
            runtime.atomic_json(version / "installation.json", {"wheel_sha256": runtime.digest(release / "dist/mynotes-test.whl")})
            (home / "app/current").symlink_to(version)
            launcher.parent.mkdir(parents=True)
            launcher.write_text("# MyNotes managed launcher\n")
        elif "verify_local_components" in command[2]:
            smoke_attempts.append(command)
            if len(smoke_attempts) == 1:
                raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(runtime.subprocess, "run", run)
    arguments = ["--release-root", str(release), "--provider", "codex"]
    assert runtime.main(arguments) == 2
    assert not launcher.exists()
    assert not (home / "app/current").exists()
    assert runtime.main(arguments) == 0
    assert "--rollback" in shell_calls[-1]
    assert launcher.is_file() and os.access(launcher, os.X_OK)
    assert "# MyNotes managed launcher" in launcher.read_text()
    output = real_run([str(launcher)], env=dict(os.environ, MYNOTES_HOME=str(home)), capture_output=True, text=True)
    assert output.returncode == 0, output.stderr
    assert "fixture launcher works" in output.stdout


def test_real_rollback_preserves_unmanaged_launcher_and_selection(tmp_path):
    import os
    import sys
    script = Path(__file__).parents[1] / "scripts/install-mynotes.sh"
    home = tmp_path / "runtime"
    app_home = home / "app"
    target = app_home / "versions/new/venv/bin/mynotes"
    target.parent.mkdir(parents=True)
    target.write_text("#!/bin/sh\nexit 0\n")
    target.chmod(0o755)
    previous = app_home / "versions/previous"
    previous.mkdir()
    (app_home / "current").symlink_to(previous)
    launcher = tmp_path / "prefix/bin/mynotes"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n# personal unrelated script\n")
    result = subprocess.run(["/bin/zsh", str(script), "--app-home", str(app_home), "--prefix", str(tmp_path / "prefix"),
                             "--python", sys.executable, "--rollback", "new"],
                            env=dict(os.environ, MYNOTES_HOME=str(home), MYNOTES_CAPTURE_HOME=str(home / "Capture")), capture_output=True, text=True)
    assert result.returncode != 0
    assert "Refusing to overwrite" in result.stderr
    assert "personal unrelated script" in launcher.read_text()
    assert (app_home / "current").resolve() == previous.resolve()



def test_reversed_shell_markers_are_rejected_without_corrupting_config(tmp_path):
    original = "# custom config\n# <<< MyNotes managed PATH <<<\nexport KEEP=yes\n# >>> MyNotes managed PATH >>>\n"
    shell = tmp_path / ".zshrc"
    shell.write_text(original)
    with pytest.raises(runtime.SetupError, match="Ambiguous"):
        runtime.ensure_shell_path(tmp_path, tmp_path / ".local")
    assert shell.read_text() == original


def test_shell_symlink_preserved_with_target_content_and_mode(tmp_path):
    target = tmp_path / "dotfiles/zshrc"
    target.parent.mkdir()
    target.write_text("# tracked custom configuration\nexport EDITOR=vim\n")
    target.chmod(0o640)
    shell = tmp_path / ".zshrc"
    shell.symlink_to(target)
    runtime.ensure_shell_path(tmp_path, tmp_path / ".local")
    assert shell.is_symlink()
    assert target.read_text().startswith("# tracked custom configuration")
    assert "MyNotes managed PATH" in target.read_text()
    assert target.stat().st_mode & 0o777 == 0o640


def test_pinned_mirror_connection_failure_falls_back(tmp_path, monkeypatch):
    spec = artifact()
    spec['mirrors'] = ['https://mirror.test/component']
    requests = []
    def open_url(request, **kwargs):
        requests.append(request.full_url)
        if len(requests) == 1:
            raise runtime.urllib.error.URLError('offline mirror')
        return Response(b'payload')
    monkeypatch.setattr(runtime.urllib.request, 'urlopen', open_url)
    assert runtime.download(spec, tmp_path/'file').read_bytes() == b'payload'
    assert requests == ['https://mirror.test/component', spec['url']]


def test_slow_pinned_mirror_resumes_exact_bytes_at_next_server(tmp_path, monkeypatch):
    from types import SimpleNamespace
    spec = artifact(b'firstsecond');spec['mirrors'] = ['https://mirror.test/component']
    calls = [];clock = iter([0,20,21,21])
    monkeypatch.setattr(runtime, 'time', SimpleNamespace(monotonic=lambda:next(clock)))
    def open_url(request, **kwargs):
        calls.append(request)
        return Response(b'first') if len(calls)==1 else Response(b'second',206,{'Content-Range':'bytes 5-10/11'})
    monkeypatch.setattr(runtime.urllib.request, 'urlopen', open_url)
    assert runtime.download(spec,tmp_path/'file').read_bytes()==b'firstsecond'
    assert calls[1].get_header('Range')=='bytes=5-'


def test_download_uses_available_stream_data_for_small_updates(tmp_path, monkeypatch):
    class Streaming(Response):
        def read(self, _):raise AssertionError('blocking large read used')
        def read1(self, size):
            assert size==64*1024
            value,self.payload=self.payload[:1],self.payload[1:]
            return value
    monkeypatch.setattr(runtime.urllib.request,'urlopen',lambda *a,**k:Streaming(b'payload'))
    updates=[]
    runtime.download(artifact(),tmp_path/'file',lambda complete,total:updates.append(complete))
    assert updates==list(range(1,8))


def test_component_progress_marks_only_real_data_and_verified_phase(tmp_path, monkeypatch):
    from types import SimpleNamespace
    counter=iter([0,0.1,0.2,1]);events=[]
    monkeypatch.setattr(runtime,'time',SimpleNamespace(time=lambda:123,monotonic=lambda:next(counter)))
    def fake_download(spec,destination,progress):
        progress(1,3);progress(2,3);progress(3,3)
        return destination
    monkeypatch.setattr(runtime,'download',fake_download)
    def emit(stage,message,complete=0,total=1,**fields):events.append(fields)
    runtime.component_download(artifact(b'abc'),tmp_path/'file',emit,'libreoffice','LibreOffice')
    assert events[0]['phase']=='download' and events[0]['last_data_at'] is None
    assert events[1]['downloaded_bytes']==1 and events[1]['last_data_at']==123
    assert events[2]['downloaded_bytes']==3
    assert events[-1]['phase']=='verify'
    assert len(events)==4


def test_component_mirrors_require_bounded_https_urls(tmp_path):
    for mirrors in [['http://mirror.test/file'],['https://mirror.test/file']*4,'https://mirror.test/file']:
        spec=artifact();spec['mirrors']=mirrors
        with pytest.raises(runtime.SetupError,match='mirrors'):
            runtime.download(spec,tmp_path/'file')


def test_successful_update_preserves_existing_optional_local_engine(tmp_path,monkeypatch):
    release,home=fake_release(tmp_path,monkeypatch)
    engine=tmp_path/'existing-local-engine';engine.write_text('fixture existing tool')
    runtime.atomic_json(home/'tools.json',{'fluidaudio':str(engine)})
    monkeypatch.setattr(runtime,'install_components',lambda *args:{'schema_version':1})
    def run(command,**kwargs):
        if command[0]=='/bin/zsh':
            v=home/'app/versions/0.2.1';(v/'venv/bin').mkdir(parents=True)
            (v/'venv/bin/python').write_text('fixture');(v/'venv/bin/mynotes').write_text('fixture')
            (home/'app/current').symlink_to(v)
        return subprocess.CompletedProcess(command,0,stdout='')
    monkeypatch.setattr(runtime.subprocess,'run',run)
    assert runtime.main(['--release-root',str(release),'--provider','codex'])==0
    assert json.loads((home/'tools.json').read_text())['fluidaudio']==str(engine)
    assert json.loads((home/'app/versions/0.2.1/managed-tools.json').read_text())['fluidaudio']==str(engine)
