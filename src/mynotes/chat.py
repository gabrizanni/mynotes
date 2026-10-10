"""Launch a real provider conversation already grounded in MyNotes."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .workspace import Workspace


class ChatError(RuntimeError):
    pass


def launch_chat(workspace: Workspace, configuration: dict[str, Any], provider: str | None, message: str | None = None) -> int:
    if os.environ.get("MYNOTES_CHAT_ACTIVE") == "1":
        raise ChatError("A MyNotes provider chat is already active; recursive launch is blocked")
    selected = provider or configuration["providers"].get("default")
    if selected not in {"codex", "agy"}:
        raise ChatError("Select a configured provider: codex or agy")
    options = configuration["providers"].get("configured", {}).get(selected, {})
    model, effort = str(options.get("model", "")).strip(), str(options.get("effort", "")).strip()
    if not model or not effort:
        raise ChatError(f"Provider {selected} needs an explicit model and effort in MyNotes configuration")
    executable = shutil.which(selected)
    if not executable:
        raise ChatError(f"Provider launcher is not on PATH: {selected}")
    source_root = Path(__file__).resolve().parents[2]
    repository = source_root if (source_root / "pyproject.toml").is_file() else workspace.root
    courses = ", ".join(course["code"] for course in configuration.get("courses", [])) or "none"
    onboarding = configuration.get("onboarding", {}).get("status", "unknown")
    language = configuration.get("personal", {}).get("interface_language", "en")
    context = (
        f"MyNotes startup context: workspace={workspace.root}; onboarding={onboarding}; "
        f"courses={courses}; provider={selected}; model={model}; effort={effort}; "
        f"interface_language={language}. "
    )
    style_guidance = (
        "If the user asks to change note style, ask only for missing preferences, use "
        "`mynotes style show` and `mynotes style edit` with `--course CODE` when the "
        "request is course-specific, then run `mynotes style preview` and show its "
        "result. A style request does not require generating or publishing a lecture. "
        "The built-in Word style is authoritative; never ask for or read a reference "
        "DOCX/PDF to apply formatting. "
    )
    if message:
        prompt = (
            context + style_guidance + "Handle this user request: " + message + ". Before taking action, run "
            "`mynotes doctor` and `mynotes status` through the command tool for this workspace; "
            "if either cannot run, report that and stop. Then inspect only task-relevant files. "
            "For a lecture request, follow prepare -> generate -> build -> publish; validate and visually inspect the Word "
            "candidate before publication. Never launch `mynotes chat` recursively."
        )
    else:
        prompt = (
            context + style_guidance + "Open the interactive session and briefly greet the user, then wait for a request. "
            "The startup context is sufficient for this greeting: do not run commands, inspect files, "
            "or explore the repository before the user asks. When a concrete task arrives, use "
            "the command tool to run `mynotes doctor` and `mynotes status` for this workspace; "
            "if either cannot run, report that and stop. Inspect only relevant sources, then for lecture work follow "
            "prepare -> generate -> build -> publish with candidate validation and visual Word review. "
            "Never launch `mynotes chat` recursively."
        )
    if selected == "agy":
        command = [executable, "--agent", "mynotes", "--model", model, "--effort", effort,
                   "--mode", "accept-edits", "--add-dir", str(workspace.root), "--prompt-interactive", prompt]
    else:
        command = [executable, "-C", str(repository), "--add-dir", str(workspace.root), "--model", model,
                   "-c", f'model_reasoning_effort="{effort}"', prompt]
    environment = os.environ.copy(); environment["MYNOTES_WORKSPACE"] = str(workspace.root); environment["MYNOTES_CHAT_ACTIVE"] = "1"
    return subprocess.run(command, env=environment, cwd=repository, check=False).returncode
