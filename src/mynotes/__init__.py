"""MyNotes: application code independent of each user's private workspace."""

from .workspace import WORKSPACE_SCHEMA_VERSION, Workspace, WorkspaceError

__all__ = ["WORKSPACE_SCHEMA_VERSION", "Workspace", "WorkspaceError"]

__version__ = "0.2.23"
