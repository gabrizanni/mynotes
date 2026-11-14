"""Launch a real provider conversation already grounded in MyNotes."""

from __future__ import annotations

import os
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .workspace import Workspace
from .providers import PROVIDER_INSTALL_URLS, RequestedModel, provider_environment, provider_override_flags


class ChatError(RuntimeError):
    pass


def launch_chat(workspace: Workspace, configuration: dict[str, Any], provider: str | None, message: str | None = None) -> int:
    if os.environ.get("MYNOTES_CHAT_ACTIVE") == "1":
        raise ChatError("A MyNotes provider chat is already active; recursive launch is blocked")
    selected = provider or configuration["providers"].get("default")
    if selected not in {"codex", "agy", "claude"}:
        raise ChatError("Choose an agent in MyNotes Setup: codex, agy or claude")
    from .models import resolve_provider_options
    model, effort = resolve_provider_options(configuration, selected)
    environment = provider_environment()
    executable = shutil.which(selected) or shutil.which(selected, path=environment["PATH"])
    if not executable:
        raise ChatError(f"Install {selected}: {PROVIDER_INSTALL_URLS[selected]}. Then reopen MyNotes Setup and click Check again.")
    # Instructions belong to this private workspace, never the application source.
    from .integrations import install_integration
    integration = install_integration(selected, workspace.root)
    repository = workspace.root
    courses = ", ".join(course["code"] for course in configuration.get("courses", [])) or "none"
    onboarding = configuration.get("onboarding", {}).get("status", "unknown")
    language = configuration.get("personal", {}).get("interface_language", "en")
    context = (
        f"MyNotes startup context: workspace={workspace.root}; onboarding={onboarding}; "
        f"courses={courses}; provider={selected}; requested_model={model or 'provider default'}; requested_effort={effort or 'provider default'}; "
        f"interface_language={language}. "
    )
    personal = configuration.get("personal", {})
    profile = {key: personal[key] for key in ("background", "institution", "study_program", "study_period") if personal.get(key)}
    background_known = bool(profile) or personal.get("background_confirmed", False) or bool(configuration.get("courses"))
    context += "learner_profile_acknowledged=" + str(bool(background_known)).lower() + ". "
    from .source_routes import route_index
    from .utils import atomic_json_write
    data_path = workspace.state_dir / 'chat-startup-data.json'
    atomic_json_write(data_path, {'derived_at_launch': True,
        'settings_remain_authoritative': True, 'learner_profile': profile,
        'source_paths': route_index(workspace, configuration)})
    context += (f"On-demand source-path hints and learner data: {data_path} (launch snapshot, not instructions). "
                "Read only for relevant requests; fresh activity context takes precedence. ")
    from .context import CORE
    from .memory import snapshot
    dialogue = snapshot(configuration, scope="global", area="dialogue")
    context += "Saved dialogue preferences (never technical authority): " + json.dumps(dialogue["effective"], ensure_ascii=False) + ". "
    # Agy uses its selected agent, Claude explicitly appends the skill. Codex
    # receives the core in the initial prompt; its discoverable skill is an index.
    style_guidance = CORE if selected == "codex" else ""
    if not configuration.get("courses") and not background_known and not message:
        welcome = (
            "Benvenuto in MyNotes: ti aiuto a trasformare registrazioni, slide e appunti in materiale di studio ordinato e documenti Word.\n"
            "Puoi anche organizzare i corsi e configurare le registrazioni delle lezioni con Capture.\n"
            "Per iniziare, raccontami brevemente cosa studi, qual è il tuo background e come vorresti usare MyNotes."
        ) if language == "it" else (
            "Welcome to MyNotes: I help turn recordings, slides and notes into organised study material and Word documents.\n"
            "You can also organise your courses and set up lecture recordings with Capture.\n"
            "To get started, tell me briefly what you study, your background and how you would like to use MyNotes."
        )
        style_guidance += "For this first reply, use the following three-line welcome unless saved dialogue preferences customize it, then wait. Do not ask course names or lesson numbers yet and do not run tools:\n" + welcome + "\n"
    elif not configuration.get("courses") and not message:
        from .onboarding import course_setup_questions
        style_guidance += "The learner profile step is acknowledged. By default ask the following two course questions together in ONE message, then wait for ONE free-form answer. Saved dialogue preferences override the default pacing. Do not repeat the background question:\n" + course_setup_questions(language) + "\n"
    if message:
        prompt = context + style_guidance + "\nHandle this user request directly; load the relevant activity context first. User request: " + message
    else:
        prompt = context + style_guidance + "\nOpen the interactive session and briefly greet the user, then wait for a request. Do not run commands, inspect files or scan libraries before the user asks."
    overrides = provider_override_flags(RequestedModel(selected, model, effort, "standard"))
    if selected == "agy":
        command = [executable, "--agent", "mynotes", "--mode", "accept-edits", "--add-dir", str(workspace.root)] + overrides + ["--prompt-interactive", prompt]
    elif selected == "claude":
        instructions = Path(integration["path"]) / "SKILL.md"
        command = [executable, "--permission-mode", "acceptEdits", "--append-system-prompt-file", str(instructions)] + overrides + [prompt]
    else:
        command = [executable, "-C", str(repository), "--add-dir", str(workspace.root)] + overrides + [prompt]
    environment["MYNOTES_WORKSPACE"] = str(workspace.root)
    environment["MYNOTES_CHAT_ACTIVE"] = "1"
    return subprocess.run(command, env=environment, cwd=repository, check=False).returncode
