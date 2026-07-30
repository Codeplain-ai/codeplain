"""Read/write helpers for the per-module metadata file (``module_metadata.json``).

These helpers operate purely on a metadata path or a plain dict. Module-specific
logic — computing the hashes, assembling the payload from the spec and required
modules — stays on :class:`plain_modules.PlainModule`.
"""

from __future__ import annotations

import json
import os

MODULE_METADATA_FILENAME = "module_metadata.json"
MODULE_FUNCTIONALITIES = "functionalities"
REQUIRED_MODULES_FUNCTIONALITIES = "required_modules_functionalities"


def load_metadata(metadata_path: str) -> dict | None:
    """Return the parsed metadata dict, or None if the file does not exist."""
    if not os.path.exists(metadata_path):
        return None

    with open(metadata_path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_metadata(metadata_path: str, metadata: dict) -> None:
    """Write metadata as indented JSON, creating the parent folder if needed."""
    os.makedirs(os.path.dirname(metadata_path), exist_ok=True)

    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=4)


def truncate_functionalities(metadata: dict, keep_count: int) -> bool:
    """Trim the functionalities list in ``metadata`` in place to ``keep_count`` entries.

    Returns True if the list was shortened, False if it was already short enough
    (in which case ``metadata`` is left untouched).
    """
    functionalities = metadata.get(MODULE_FUNCTIONALITIES, [])
    if len(functionalities) <= keep_count:
        return False

    metadata[MODULE_FUNCTIONALITIES] = functionalities[:keep_count]
    return True
