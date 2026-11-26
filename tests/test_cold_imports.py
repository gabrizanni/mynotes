"""Quick commands start without Word, PDF, slide or audio libraries."""
import json
import subprocess
import sys

import pytest

HEAVY = ("docx", "lxml", "pypdfium2", "PIL", "av", "pptx", "openpyxl")


def loaded_after(code):
    probe = code + f"; import json; print(json.dumps(sorted(m for m in {HEAVY!r} if m in sys.modules)))"
    completed = subprocess.run([sys.executable, "-c", "import sys; " + probe], capture_output=True, text=True, check=True)
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_cli_import_loads_no_heavy_library():
    assert loaded_after("import mynotes.cli") == []


def test_parser_and_version_load_no_heavy_library():
    assert loaded_after("import mynotes.cli as cli; cli.build_parser()") == []


def test_deferred_names_keep_their_identity():
    from mynotes import cli, publication, sourcepack, extraction
    assert cli.PublicationError is publication.PublicationError
    assert cli.build_word_candidate is publication.build_word_candidate
    assert cli.restore_backup is publication.restore_backup
    assert cli.SourcePackError is sourcepack.SourcePackError
    assert cli.ExtractionError is extraction.ExtractionError
    with pytest.raises(AttributeError):
        cli.not_a_command_name


def test_deferred_errors_are_still_reported_as_plain_failures(tmp_path, monkeypatch, capsys):
    from mynotes import publication
    from mynotes.cli import main
    def refuse(*_args, **_kwargs):
        raise publication.PublicationError("synthetic refusal")
    monkeypatch.setattr(publication, "build_word_candidate", refuse)
    assert main(["build", "OPS-L01", "--workspace", str(tmp_path / "ws")]) == 2
    assert capsys.readouterr().out == "mynotes: synthetic refusal\n"


def test_source_pack_wrapper_forwards_and_keeps_error_handling(tmp_path, monkeypatch, capsys):
    from mynotes import sourcepack
    from mynotes.cli import main
    transcript = tmp_path / "transcript.txt"
    transcript.write_text("Synthetic speech.", encoding="utf-8")
    calls = []
    def refuse(*args, **kwargs):
        calls.append(args[1])
        raise sourcepack.SourcePackError("synthetic pack refusal")
    monkeypatch.setattr(sourcepack, "build_source_pack", refuse)
    assert main(["prepare", "OPS-L01", "--workspace", str(tmp_path / "ws"), "--transcript", str(transcript)]) == 2
    assert calls == ["OPS-L01"]
    assert capsys.readouterr().out == "mynotes: synthetic pack refusal\n"


def test_one_parser_serves_repeated_calls(tmp_path, capsys):
    from mynotes import cli
    workspace = str(tmp_path / "ws")
    assert cli.main(["config", "show", "--json", "--workspace", workspace]) == 0
    first = json.loads(capsys.readouterr().out)
    parser = cli._shared_parser()
    assert cli.main(["course", "pending", "--json", "--workspace", workspace]) == 0
    assert json.loads(capsys.readouterr().out) == {"courses": []}
    assert cli.main(["config", "show", "--json", "--workspace", workspace]) == 0
    assert json.loads(capsys.readouterr().out) == first
    assert cli._shared_parser() is parser
