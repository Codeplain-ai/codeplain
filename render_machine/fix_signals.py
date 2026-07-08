"""Signals computed over the conformance fix loop.

Two pure helpers that give the fix loop a memory of what already happened, so the
agent cannot burn attempts repeating itself unnoticed:

- compute_failure_signature: a stable fingerprint of a failing test run. If the
  fingerprint is unchanged after a fix, the fix did not move the failure — either
  the edit never reached the executed code or the hypothesis class is wrong. The
  fix loop tells the agent so explicitly instead of letting it rediscover it.

- find_duplicate_attempt: detects a submit_fix whose description matches an attempt
  the ledger already records as failed or rejected, so it can be bounced back to
  the agent before an expensive test/review cycle is spent on it.

Both are language- and project-agnostic: they work on raw test-runner output and on
the agent's own submission text, not on any specific toolchain.
"""

import difflib
import hashlib
import re

# Lines that carry failure identity in test-runner output, across ecosystems
# (JUnit, pytest, jest, go test, ...): anything mentioning failures, errors,
# exceptions or assertions.
_FAILURE_LINE_RE = re.compile(r"(?i)\b(fail(?:ed|ure)?s?|errors?|exceptions?|assert(?:ion)?s?)\b")

# Cap the number of lines that feed the fingerprint so pathological outputs stay cheap.
_MAX_SIGNATURE_LINES = 200

# Volatile fragments that change run-to-run without the failure changing.
_VOLATILE_PATTERNS = (
    (re.compile(r"\d+(?:\.\d+)?\s*(?:ms|s|sec|secs|seconds|min|mins|minutes)\b", re.IGNORECASE), "<t>"),
    (re.compile(r"0x[0-9a-f]+", re.IGNORECASE), "<addr>"),
    (re.compile(r"(?:/private)?(?:/var/folders|/tmp)/\S+"), "<tmp>"),
    (re.compile(r"@[0-9a-f]{6,}", re.IGNORECASE), "<id>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\S*"), "<ts>"),
)

# How similar (0..1) a new submission's description must be to a failed ledger
# entry's to be considered a resubmission of the same approach.
DUPLICATE_SIMILARITY_THRESHOLD = 0.85
# Below this many characters of combined description, similarity is meaningless.
_MIN_COMPARABLE_CHARS = 24


def compute_failure_signature(test_output: str) -> str | None:
    """Fingerprint the failure identity of a test run's output.

    The signature is built from the normalized set of failure-carrying lines
    (order-independent, volatile fragments like durations/timestamps/temp paths
    masked), so two runs that fail the same way produce the same signature even
    when incidental output differs. Returns None when no failure lines are found.
    """
    if not test_output:
        return None

    lines: set[str] = set()
    for line in test_output.splitlines():
        if not _FAILURE_LINE_RE.search(line):
            continue
        normalized = " ".join(line.split())
        for pattern, replacement in _VOLATILE_PATTERNS:
            normalized = pattern.sub(replacement, normalized)
        lines.add(normalized)
        if len(lines) >= _MAX_SIGNATURE_LINES:
            break

    if not lines:
        return None
    digest = hashlib.sha1("\n".join(sorted(lines)).encode("utf-8", "replace")).hexdigest()
    return digest[:16]


def _attempt_description(entry: dict) -> str:
    text = " ".join(str(entry.get(key) or "") for key in ("root_cause", "changes_made"))
    return " ".join(text.lower().split())


def find_duplicate_attempt(
    fix_summary: dict, ledger: list, threshold: float = DUPLICATE_SIMILARITY_THRESHOLD
) -> int | None:
    """Return the ledger index of a failed/rejected attempt this submission repeats.

    Only entries with a recorded outcome are compared (an outcome means the attempt
    demonstrably did not resolve the failure). Comparison is text similarity over the
    submission's root_cause + changes_made; short descriptions are skipped because
    similarity over a few words is noise.
    """
    candidate = _attempt_description(fix_summary)
    if len(candidate) < _MIN_COMPARABLE_CHARS:
        return None

    for index, entry in enumerate(ledger):
        if not entry.get("outcome"):
            continue
        prior = _attempt_description(entry)
        if len(prior) < _MIN_COMPARABLE_CHARS:
            continue
        if difflib.SequenceMatcher(None, candidate, prior).ratio() >= threshold:
            return index
    return None
