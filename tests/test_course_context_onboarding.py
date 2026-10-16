import json
import subprocess

import pytest

from mynotes.chat import launch_chat
from mynotes.cli import main
from mynotes.configuration import effective_style
from mynotes.onboarding import add_first_course, prepare_conversation
from mynotes.preferences import save_background, save_preferences
from mynotes.providers import ProviderRequest, RequestedModel, _context, _editorial_instructions
from mynotes.workspace import Workspace


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("MYNOTES_HOME", str(tmp_path / "runtime"))
    monkeypatch.setenv("MYNOTES_USER_HOME", str(tmp_path / "user"))
    monkeypatch.delenv("MYNOTES_CHAT_ACTIVE", raising=False)
    result = Workspace(tmp_path / "private")
    prepare_conversation(result, "codex")
    save_background(result, "I study economics and need explanations with examples.")
    return result


def test_one_course_answer_reaches_saved_settings_and_generation_context(workspace, capsys):
    answer = "Financial Mathematics: esercizi ed esame scritto. Lezioni in inglese, appunti in italiano. Voglio spiegazioni approfondite, schemi finali e poche figure: massimo 3."
    interpreted = {"kind": "quantitative", "assessment": "Written exercises", "preparation": "Understand each calculation step",
                   "languages": {"lesson": "en", "notes": "it"}, "difficult_topics": ["Discounting"],
                   "style": {"writing_style": "detailed", "list_density": "prose_first", "image_policy": "essential"},
                   "preferences": {"persistent_instructions": ["Schemi finali e massimo 3 figure pertinenti per lezione."]}}
    assert main(["course", "add", "--workspace", str(workspace.root), "--name", "Financial Mathematics",
                 "--context-answer", answer, "--settings-json", json.dumps(interpreted)]) == 0
    course = json.loads(capsys.readouterr().out)
    saved = workspace.configuration_store().load()
    assert len(saved["courses"]) == 1
    assert course["context"] == answer
    assert course["languages"] == {"lesson": "en", "notes": "it"}
    assert course["assessment"] == "Written exercises"
    style = effective_style(saved, course["code"])
    assert style["writing_style"] == "detailed"
    assert style["list_density"] == "prose_first"
    assert style["image_policy"] == "essential"
    request = ProviderRequest("TEST-L01", course, {}, RequestedModel("codex", "", "", "standard"), style=style)
    context = _context(request)
    assert "Financial Mathematics" in context and "Discounting" in context and "massimo 3" in context
    instructions = " ".join(_editorial_instructions(style, "it"))
    assert "Write detailed" in instructions and "Prefer connected paragraphs" in instructions
    assert "maximum, not a quota" in instructions


def test_updating_course_context_merges_only_supplied_preferences(workspace, tmp_path):
    first = add_first_course(workspace, "Design Methods", "it", context_answer="Cases with a final project",
                             course_settings={"kind": "cases", "assessment": "Final project",
                                              "style": {"image_policy": "explanatory", "writing_style": "detailed"}})
    other = add_first_course(workspace, "Applied Statistics", "en")
    old_candidate = workspace.runs_dir / (first["code"] + "-L01") / "candidate.json"
    old_candidate.parent.mkdir(parents=True)
    old_candidate.write_text("keep previous candidate")
    updated = add_first_course(workspace, "design methods", context_answer="Prefer more outlines now",
                               course_settings={"style": {"list_density": "list_first"}})
    saved = workspace.configuration_store().load()
    assert updated["code"] == first["code"] and updated["folders"] == first["folders"]
    assert updated["assessment"] == "Final project"
    assert updated["languages"] == first["languages"]
    assert updated["style"]["image_policy"] == "explanatory"
    assert updated["style"]["writing_style"] == "detailed"
    assert updated["style"]["list_density"] == "list_first"
    assert next(c for c in saved["courses"] if c["code"] == other["code"]) == other
    assert len(saved["courses"]) == 2 and old_candidate.read_text() == "keep previous candidate"


def test_explicit_lecture_language_is_used_for_transcription(workspace):
    course = add_first_course(workspace, "History", "en", context_answer="Lezioni in italiano, appunti in inglese",
                             course_settings={"languages": {"lesson": "it"}})
    assert course["languages"] == {"lesson": "it", "notes": "en"}
    assert course["transcription"]["language"] == "it"


@pytest.mark.parametrize("settings", [None, [], {"folders": {"materials": "replace folders"}},
                                     {"style": {"image_policy": "invented"}}, {"assessment": None},
                                     {"kind": "unknown"}, {"languages": {"lesson": ""}}])
def test_invalid_interpreted_answer_does_not_change_configuration(workspace, settings):
    before = workspace.configuration_path.read_bytes()
    assert main(["course", "add", "--workspace", str(workspace.root), "--name", "An example course",
                 "--context-answer", "An original answer", "--settings-json", json.dumps(settings)]) == 2
    assert workspace.configuration_path.read_bytes() == before


def test_saved_background_is_followed_by_two_course_questions_together(workspace, monkeypatch):
    save_preferences(workspace, "codex", "", "it")
    calls = []
    monkeypatch.setattr("mynotes.chat.shutil.which", lambda *args, **kwargs: "/bin/codex")
    monkeypatch.setattr("mynotes.chat.subprocess.run", lambda cmd, **kwargs: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    launch_chat(workspace, workspace.configuration_store().load(), "codex")
    prompt = calls[0][-1]
    assert "two course questions together in ONE message" in prompt
    assert "wait for ONE free-form answer" in prompt
    assert "1. Che corso è" in prompt and "2. Come vorresti gli appunti" in prompt
    assert "nessuna immagine, poche essenziali o più figure spiegate" in prompt
    assert "Do not repeat the background question" in prompt
