"""Check CLI launch byte limits without truncating academic evidence."""
from __future__ import annotations

import os
import struct
import sys
from collections.abc import Mapping, Sequence


class CommandSizeError(ValueError):
    """A request cannot safely fit into an operating-system CLI launch."""


def _positive_sysconf(name: str) -> int | None:
    try:
        value = os.sysconf(name)
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, int) and value > 0 else None


def validate_command_size(command: Sequence[str], environment: Mapping[str, str]) -> dict[str, int | None]:
    """Preflight argv/environment limits; counts are bytes, never token estimates.

    Leave ten percent for platform bookkeeping. Unknown limits remain unknown.
    Errors contain only counts and an action, never prompt or environment text.
    """
    argument_sizes = [len(os.fsencode(value)) + 1 for value in command]
    environment_sizes = [len(os.fsencode(key + "=" + value)) + 1 for key, value in environment.items()]
    pointer_bytes = (len(command) + len(environment) + 2) * struct.calcsize("P")
    estimated = sum(argument_sizes) + sum(environment_sizes) + pointer_bytes
    maximum = _positive_sysconf("SC_ARG_MAX")
    safe_limit = maximum * 9 // 10 if maximum is not None else None
    single_limit = None
    if sys.platform.startswith("linux"):
        pagesize = _positive_sysconf("SC_PAGESIZE")
        single_limit = pagesize * 32 if pagesize is not None else None
    oversized_single = single_limit is not None and any(size > single_limit for size in argument_sizes + environment_sizes)
    if oversized_single or (safe_limit is not None and estimated > safe_limit):
        raise CommandSizeError(
            "Local CLI input limit: request needs approximately " + str(estimated)
            + " bytes; safe launch limit is " + str(safe_limit)
            + " bytes, single-string limit is " + str(single_limit)
            + ". Reduce the selected material or split the lesson before generating. "
            + "The provider was not started and no source was truncated."
        )
    return {"estimated_launch_bytes": estimated, "safe_launch_limit_bytes": safe_limit,
            "single_string_limit_bytes": single_limit}
