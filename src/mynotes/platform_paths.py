"""Per-platform defaults for private runtime data outside any workspace."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .workspace import windows_data_home


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def linux_data_home() -> Path:
    # Same root as the default Linux workspace, as macOS shares one folder too.
    return Path.home() / ".local" / "share" / "mynotes"


def default_runtime_home() -> Path:
    """Where tools, model caches and checks live unless MYNOTES_HOME is set."""
    if os.name == "nt":
        return windows_data_home()
    if is_linux():
        return linux_data_home()
    return Path.home() / "Library/Application Support/MyNotes"


def linux_config_home() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME")
    # The XDG specification ignores relative values.
    if configured and Path(configured).is_absolute():
        return Path(configured)
    return Path.home() / ".config"
