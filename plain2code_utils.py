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


def format_duration_hms(total_seconds: int) -> str:
    """Format a duration in seconds as hours, minutes, and seconds (e.g. ``1h 2m 3.45s``, ``45.67s``)."""
    if total_seconds < 0:
        total_seconds = 0
    h = int(total_seconds // 3600)
    m = int((total_seconds % 3600) // 60)
    s = total_seconds % 60
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    text = f"{s}".rstrip("0").rstrip(".")
    return f"{text}s" if text else "0s"


AMBIGUITY_CAUSES = {
    "reference_resource_ambiguity": "Ambiguity is in the reference resources",
    "definition_ambiguity": "Ambiguity is in the definitions",
    "non_functional_requirement_ambiguity": "Ambiguity is in the implementation reqs",
    "functional_requirement_ambiguity": "Ambiguity is in the functionality",
    "other": "Ambiguity in the other parts of the specification",
}
