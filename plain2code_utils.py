import re
from typing import Optional

#
MAX_BASE64_BLOB_LENGTH = 8192

# Matches a long contiguous base64 / base64url run, optionally preceded by a data: URI header.
_BASE64_BLOB_PATTERN = re.compile(
    r"(?:data:[\w.+-]+/[\w.+-]+;base64,)?[A-Za-z0-9+/_-]{%d,}={0,2}" % MAX_BASE64_BLOB_LENGTH
)


def find_large_base64_blob(text: str) -> Optional[str]:
    """Return the first contiguous base64 blob at or above the threshold, or None."""
    match = _BASE64_BLOB_PATTERN.search(text)
    return match.group(0) if match else None


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
