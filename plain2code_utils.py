from typing import Optional

from plain_parser.loaders import MAX_BASE64_BLOB_LENGTH, find_large_base64_blob  # noqa: F401


def format_duration_hms(total_seconds: float) -> str:
    """Format a whole-second duration compactly (e.g. ``10s``, ``5m 49s``, ``1h 2m``).

    Fractional seconds are truncated. Durations under a minute render as ``{s}s``,
    under an hour as ``{m}m {s}s``, and beyond as ``{h}h {m}m``.
    """
    elapsed = int(total_seconds)
    if elapsed < 0:
        elapsed = 0
    if elapsed < 60:
        return f"{elapsed}s"
    minutes = elapsed // 60
    seconds = elapsed % 60
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours = minutes // 60
    return f"{hours}h {minutes % 60}m"


AMBIGUITY_CAUSES = {
    "reference_resource_ambiguity": "Ambiguity is in the reference resources",
    "definition_ambiguity": "Ambiguity is in the definitions",
    "non_functional_requirement_ambiguity": "Ambiguity is in the implementation reqs",
    "functional_requirement_ambiguity": "Ambiguity is in the functionality",
    "other": "Ambiguity in the other parts of the specification",
}


def functionality_position(frids: list[str], frid: Optional[str]) -> Optional[str]:
    """Where a functionality stands among the functionalities of its module, as "3/12".

    The count is over every functionality of the module, not over a render range, so the number
    says where the render is in the specification whatever part of it was asked for.
    """
    if frid is None or frid not in frids:
        return None

    return f"{frids.index(frid) + 1}/{len(frids)}"


def with_position(name: Optional[str], position: Optional[str]) -> Optional[str]:
    """A functionality name followed by its position, as "list-expenses (1/3)"."""
    return f"{name} ({position})" if position else name


def qualified_frid(module_name: Optional[str], frid: Optional[str]) -> Optional[str]:
    """A functionality named as `<module>:<func-id>`.

    A func-id is unique only within its module, so this is the form to use wherever the
    module is not already named next to it. Returns the func-id alone when the module is
    unknown, and None when there is no functionality to name.
    """
    if frid is None:
        return None
    return f"{module_name}:{frid}" if module_name else frid
