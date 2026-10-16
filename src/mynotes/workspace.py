"""Private workspace layout and safe initialization for MyNotes."""

from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .resources import default_configuration
from .utils import atomic_json_write

WORKSPACE_SCHEMA_VERSION = 2
WORKSPACE_ENVIRONMENT_VARIABLE = "MYNOTES_WORKSPACE"


class WorkspaceError(RuntimeError):
    """Raised when a requested workspace is invalid or not initialized."""


@dataclass(frozen=True)
class Workspace:
    """Locations belonging to one user-private MyNotes workspace."""

    root: Path

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    @property
    def archives_dir(self) -> Path:
        return self.root / "archives"

    @property
    def migrations_dir(self) -> Path:
        return self.root / "migrations"

    @property
    def configuration_path(self) -> Path:
        return self.config_dir / "settings.json"

    @property
    def configuration_history_dir(self) -> Path:
        return self.config_dir / "history"

    @property
    def database_path(self) -> Path:
        return self.state_dir / "mynotes.sqlite3"

    @property
    def status_path(self) -> Path:
        return self.root / "STATUS.md"

    @classmethod
    def from_value(cls, value: str | os.PathLike[str] | None = None) -> "Workspace":
        if value is None:
            value = os.environ.get(WORKSPACE_ENVIRONMENT_VARIABLE)
        if value is None:
            value = default_workspace_path()
        return cls(Path(value).expanduser().resolve())

    def required_directories(self) -> Iterable[Path]:
        return (
            self.config_dir,
            self.state_dir,
            self.runs_dir,
            self.archives_dir,
            self.migrations_dir,
        )

    def is_initialized(self) -> bool:
        return self.configuration_path.is_file() and self.database_path.is_file()

    def initialize(self) -> None:
        """Create an empty private workspace without reading any legacy data."""
        # Newly created technical state must not expose notes/attempts to other
        # local accounts. Preserve permissions on any existing user directory.
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for directory in self.required_directories():
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.configuration_path.exists():
            atomic_json_write(self.configuration_path, default_configuration())
        if not self.database_path.exists():
            with sqlite3.connect(self.database_path) as connection:
                connection.execute(
                    "CREATE TABLE workspace_metadata "
                    "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO workspace_metadata(key, value) VALUES (?, ?)",
                    ("schema_version", str(WORKSPACE_SCHEMA_VERSION)),
                )
        if not self.status_path.exists():
            self.status_path.write_text(
                "# MyNotes private workspace\n\nStatus: initialized; onboarding pending.\n",
                encoding="utf-8",
            )

    def configuration_store(self):
        """Return the versioned private configuration store without writing."""
        from .configuration import ConfigurationStore

        return ConfigurationStore(self.configuration_path, self.configuration_history_dir)

    def diagnostics(self) -> list[str]:
        """Return human-readable checks without writing to the workspace."""
        results = [f"workspace: {self.root}"]
        results.append("configuration: present" if self.configuration_path.is_file() else "configuration: missing")
        results.append("state database: present" if self.database_path.is_file() else "state database: missing")
        if self.configuration_path.is_file():
            try:
                configuration = self.configuration_store().load()
                results.append(f"onboarding: {configuration['onboarding']['status']}")
                results.append(f"courses: {len(configuration['courses'])}")
                for provider in sorted(configuration["providers"]["configured"]):
                    results.append(f"provider {provider}: {shutil.which(provider) or 'NOT FOUND'}")
                for directory in configured_directories(configuration):
                    results.append(f"path {'ok' if directory.exists() else 'MISSING'}: {directory}")
            except Exception as error:
                results.append(f"configuration validation: FAILED ({error})")
        soffice = shutil.which("soffice")
        bundled = list((Path.home() / ".cache" / "codex-runtimes").glob("*/dependencies/bin/override/soffice"))
        results.append(f"word renderer: {soffice or (str(bundled[0]) if bundled else 'NOT FOUND')}")
        transactions = self.state_dir / "publication-transactions"
        incomplete = 0
        if transactions.is_dir():
            import json
            for path in transactions.glob("*.json"):
                try:
                    if json.loads(path.read_text(encoding="utf-8")).get("status") not in {"COMPLETED", "ABORTED", "NEEDS_REVIEW"}:
                        incomplete += 1
                except Exception:
                    incomplete += 1
        results.append(f"incomplete publication transactions: {incomplete}")
        return results


def default_workspace_path() -> Path:
    """Use platform-local application data rather than a source checkout."""
    if os.name == "posix" and Path("/System/Library/CoreServices").exists():
        return Path.home() / "Library" / "Application Support" / "MyNotes"
    return Path.home() / ".local" / "share" / "mynotes"


def configured_directories(configuration):
    """Local import wrapper avoids a configuration/workspace import cycle."""
    from .configuration import configured_directories as values
    return values(configuration)
