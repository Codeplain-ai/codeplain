"""Normalization of raw test-runner output into a stable failure fingerprint.

The requirement here is *stability*, not semantic perfection: the same underlying failure
must produce the same fingerprint across attempts, even though the raw output differs in
paths, line numbers, timings and object addresses between runs.

Everything in this module is a pure function. Console-clearing escape codes are already
stripped upstream by ``render_machine.render_utils._sanitize_script_output``; this module
handles the run-to-run variation that survives that step.
"""

from __future__ import annotations

import hashlib
import re
from typing import Optional

FINGERPRINT_LENGTH = 12
SIGNATURE_MAX_LINES = 5
EXCERPT_MAX_CHARS = 1500

# Applied in order. Earlier patterns must consume the structured tokens (timestamps,
# UUIDs, hashes, paths) before the generic number scrubber reaches them, otherwise the
# generic pattern shreds them into unrecognisable fragments.
_SCRUBBERS: list[tuple[re.Pattern, str]] = [
    # Residual ANSI escape sequences (colour codes, cursor movement).
    (re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]"), ""),
    # ISO-8601 and common log timestamps.
    (
        re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"),
        "<TS>",
    ),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\b"), "<TS>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "<DATE>"),
    # UUIDs before hashes, hashes before addresses, all before generic numbers.
    (
        re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
        "<UUID>",
    ),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<ADDR>"),
    (re.compile(r"\b[0-9a-f]{7,40}\b"), "<HEX>"),
    # Paths collapse to their basename: the file name carries signal, the directory
    # prefix (build folder, temp dir, CI workspace) changes on every run. This must run
    # before any temp-directory handling, otherwise the prefix rule would swallow the
    # basename along with the prefix.
    (re.compile(r"(?:[A-Za-z]:)?(?:\\[^\s\\:,)\"']+)+\\([^\s\\:,)\"']+)"), r"\1"),
    (re.compile(r"/(?:[^\s/:,)\"']+/)+([^\s/:,)\"']+)"), r"\1"),
    # Randomly named temp files/dirs that survived as a basename (pytest tmpdir and friends).
    (re.compile(r"\btmp[0-9a-z_]{4,}\b", re.IGNORECASE), "<TMP>"),
    # Elapsed times and durations.
    (
        re.compile(r"\b\d+(?:[.,]\d+)?\s?(?:ms|us|ns|s|sec|secs|seconds|m|min|mins|h|hrs)\b"),
        "<DUR>",
    ),
    # file.py:123:45 style source references.
    (re.compile(r":\d+(?::\d+)?\b"), ":<N>"),
    # Anything numeric that survived the above.
    (re.compile(r"\b\d+(?:[.,]\d+)?\b"), "<N>"),
    # Collapse whitespace runs so indentation changes do not alter the fingerprint.
    (re.compile(r"[ \t]+"), " "),
]

# Lines matching any of these carry the actual failure. Ordered scanning of these markers
# is what keeps the signature focused on the error rather than on runner chatter.
_FAILURE_MARKERS: list[re.Pattern] = [
    re.compile(r"\bassert(?:ion)?\s*(?:error|failed)?\b", re.IGNORECASE),
    re.compile(r"\b\w*(?:Error|Exception)\b"),
    re.compile(r"^\s*E\s{2,}"),  # pytest's error continuation lines
    re.compile(r"\bFAILED?\b"),
    re.compile(r"\bFAILURE!?\b", re.IGNORECASE),
    re.compile(r"\bTraceback\b"),
    re.compile(r"^\s*panic:"),  # go
    re.compile(r"\bexpected\b.*\b(?:but|actual|got|received)\b", re.IGNORECASE),
    re.compile(r"^\s*×"),  # vitest / jest failure bullets
]


def normalize_output(raw_output: str) -> str:
    """Scrub every run-to-run varying token out of test-runner output."""
    normalized = raw_output
    for pattern, replacement in _SCRUBBERS:
        normalized = pattern.sub(replacement, normalized)
    return normalized


def _is_failure_line(line: str) -> bool:
    return any(marker.search(line) for marker in _FAILURE_MARKERS)


def extract_signature(normalized_output: str) -> str:
    """Pick the lines that identify the failure.

    Failure-marker lines are preferred. When a runner emits none that we recognise, the
    tail of the output is used instead, since test runners put their summary last.
    """
    lines = [line.strip() for line in normalized_output.splitlines() if line.strip()]
    if not lines:
        return ""

    signature_lines: list[str] = []
    for line in lines:
        if _is_failure_line(line) and line not in signature_lines:
            signature_lines.append(line)
            if len(signature_lines) == SIGNATURE_MAX_LINES:
                break

    if not signature_lines:
        signature_lines = lines[-SIGNATURE_MAX_LINES:]

    return "\n".join(signature_lines)


def compute_fingerprint(signature: str) -> Optional[str]:
    """Hash a signature into a short stable identifier. Empty signature has no identity."""
    if not signature:
        return None
    return hashlib.sha256(signature.encode("utf-8")).hexdigest()[:FINGERPRINT_LENGTH]


def _build_excerpt(normalized_output: str) -> str:
    """Bound the normalized output, keeping the tail where failures are reported."""
    excerpt = normalized_output.strip()
    if len(excerpt) <= EXCERPT_MAX_CHARS:
        return excerpt
    return "...\n" + excerpt[-EXCERPT_MAX_CHARS:]


def fingerprint_output(raw_output: Optional[str]) -> tuple[Optional[str], str, str]:
    """Normalize test output into ``(fingerprint, signature, excerpt)``.

    A passing or empty run has no failure identity and yields ``(None, "", "")``.
    """
    if not raw_output or not raw_output.strip():
        return None, "", ""

    normalized = normalize_output(raw_output)
    signature = extract_signature(normalized)
    return compute_fingerprint(signature), signature, _build_excerpt(normalized)
