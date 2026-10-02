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
    repository = Path(__file__).resolve().parents[2]
    prompt = message or (
        "Open an interactive MyNotes session. First run `mynotes doctor` and `mynotes status`. "
        "Then ask what the user wants to do in natural language. Use prepare -> generate -> build -> publish; "
        "never publish without a validated Word candidate and never launch `mynotes chat` recursively."
    )
    if selected == "agy":
        command = [executable, "--agent", "mynotes", "--model", model, "--effort", effort,
                   "--mode", "accept-edits", "--add-dir", str(workspace.root), "--prompt-interactive", prompt]
    else:
        command = [executable, "-C", str(repository), "--add-dir", str(workspace.root), "--model", model,
                   "-c", f'model_reasoning_effort="{effort}"', prompt]
    environment = os.environ.copy(); environment["MYNOTES_WORKSPACE"] = str(workspace.root); environment["MYNOTES_CHAT_ACTIVE"] = "1"
    return subprocess.run(command, env=environment, cwd=repository, check=False).returncode
