"""Path resolution for CLI / config / default path arguments.

The rule, in one sentence: the resolution base for a relative path is determined
by *where the value was written*, not by which option key it sets.

- Values supplied on the command line resolve against the current working
  directory (so they match what shell tab completion just produced).
- Values read from ``config.yaml`` resolve against the directory containing
  that config file.
- Values left at their default (not supplied anywhere) resolve against the
  directory containing the spec file.

Paths are expanded for ``~`` and returned as absolute paths. Canonicalization
(symlink resolution) is left to the caller, to be done just before I/O that
needs it (writing output, executing scripts) so we don't accidentally
dereference links the user wanted preserved.
"""

import os
from typing import Literal, Optional

PathSource = Literal["cli", "config", "default"]


def resolve_path(
    value: str,
    source: PathSource,
    *,
    cwd: str,
    config_dir: Optional[str] = None,
    spec_dir: str,
) -> str:
    """Resolve *value* to an absolute path using the base anchor for *source*.

    Args:
        value: The path string as written by the user. May be absolute, relative,
            or contain a leading ``~``.
        source: Where the value came from -- ``"cli"``, ``"config"``, or
            ``"default"``.
        cwd: Base directory for ``"cli"`` values.
        config_dir: Base directory for ``"config"`` values. Must be provided
            whenever ``source == "config"``.
        spec_dir: Base directory for ``"default"`` values.

    Returns:
        Absolute path with ``~`` expanded. Symlinks are not resolved.
    """
    expanded = os.path.expanduser(value)

    if os.path.isabs(expanded):
        return os.path.normpath(expanded)

    if source == "cli":
        base = cwd
    elif source == "config":
        if config_dir is None:
            raise ValueError("config_dir must be provided when source == 'config'")
        base = config_dir
    elif source == "default":
        base = spec_dir
    else:
        raise ValueError(f"Unknown path source: {source!r}")

    return os.path.normpath(os.path.join(base, expanded))
