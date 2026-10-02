"""Package-owned, distributable MyNotes resources."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any


def default_configuration() -> dict[str, Any]:
    """Return a new copy of the distributable, user-neutral configuration."""
    resource = files(__name__).joinpath("defaults.json")
    return json.loads(resource.read_text(encoding="utf-8"))
