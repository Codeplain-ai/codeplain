"""Extraction of a failure's identity and description from raw test-runner output.

A failure is reduced to a short list of **cause lines** - the text the runner emitted that
says what actually went wrong. Those lines serve three purposes at once: hashed they give
the failure its identity, tokenized they are what lexical retrieval scores, and read
directly they are how a failure is described in a prompt.

Two design points follow from that, and both were learned from real output:

* **Candidacy is narrow.** Stack frames, run-count summaries and build banners are
  excluded. A Spring context-load failure buries its root cause under a 1500-character
  configuration dump, and including the wrapper made two identical failures look different
  because the dump embeds the test class name.
* **Cause lines keep their numbers.** ``expected: <200> but was: <401>`` and
  ``... but was: <500>`` are different failures, so the aggressive number scrubbing that
  keeps a whole log stable would erase exactly the values that distinguish them. Volatile
  tokens still go - timestamps, addresses, object hashes, paths, line references - but they
  are removed by structured rules that know what they are matching, not by a catch-all.

The fallback path, used when no rule recognises the output, keeps the aggressive scrubbing:
there the text is arbitrary and stability matters more than legibility.

Every function here is pure. Console-clearing escape codes are stripped upstream by
``render_machine.render_utils._sanitize_script_output``.
"""

from __future__ import annotations

import hashlib
import re
from typing import Optional

FINGERPRINT_LENGTH = 12

# A failure can have several independent causes - several failing tests, several compile
# errors. Listing a few covers that without turning a record back into a log.
MAX_CAUSES = 5
CAUSE_MAX_CHARS = 200

# Volatile tokens that must go from a cause line, each matched by a rule that knows what it
# is removing. Ordered: structured tokens are consumed before anything more general.
_CAUSE_SCRUBBERS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]"), ""),
    (
        re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"),
        "<TS>",
    ),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\b"), "<TS>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "<DATE>"),
    (
        re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
        "<UUID>",
    ),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<ADDR>"),
    # Object identity hashes, as in `WebMergedContextConfiguration@4d9e68d0`.
    (re.compile(r"@[0-9a-f]{6,40}\b"), "@<HASH>"),
    # Paths collapse to their basename: the file name carries signal, the directory prefix
    # (build folder, temp dir, CI workspace, local home) changes between runs and machines.
    (re.compile(r"(?:[A-Za-z]:)?(?:\\[^\s\\:,)\"']+)+\\([^\s\\:,)\"']+)"), r"\1"),
    (re.compile(r"/(?:[^\s/:,)\"']+/)+([^\s/:,)\"']+)"), r"\1"),
    (re.compile(r"\btmp[0-9a-z_]{4,}\b", re.IGNORECASE), "<TMP>"),
    (re.compile(r"[ \t]+"), " "),
]

# The aggressive set, used only on the fallback path where the text is unrecognised and
# stability is the only property available.
_FALLBACK_SCRUBBERS: list[tuple[re.Pattern, str]] = _CAUSE_SCRUBBERS[:-1] + [
    (
        re.compile(r"\b\d+(?:[.,]\d+)?\s?(?:ms|us|ns|s|sec|secs|seconds|m|min|mins|h|hrs)\b"),
        "<DUR>",
    ),
    (re.compile(r":\d+(?::\d+)?\b"), ":<N>"),
    (re.compile(r"\b\d+(?:[.,]\d+)?\b"), "<N>"),
    (re.compile(r"[ \t]+"), " "),
]

# Lines that can never be a cause, however much they look like one.
_EXCLUDED_LINES: list[re.Pattern] = [
    re.compile(r"^\s*at\s+\S+\("),  # JVM / JS stack frame
    re.compile(r'^\s*File "'),  # Python traceback frame
    re.compile(r"^\s*at\s+\S+\.(?:java|kt|scala|js|ts):\d+"),
    re.compile(r"^\s*\S+\.go:\d+ \+0x"),  # Go stack frame
    re.compile(r"\bTests?\s+run:\s*\d+", re.IGNORECASE),  # Surefire count summary
    re.compile(r"\b\d+\s+(?:failed|passed|skipped)\b", re.IGNORECASE),  # pytest/jest tallies
    re.compile(r"\bBUILD\s+(?:FAILURE|SUCCESS)\b"),
    re.compile(r"^\s*(?:\[INFO\]\s*)?(?:Total time|Finished at)\s*:"),
    re.compile(r"\bPlease refer to\b|\bTo see the full stack trace\b|\bRe-run Maven\b"),
    re.compile(r"->\s*\[Help\s*\d+\]"),
    re.compile(r"^\s*[-=]{3,}\s*$"),
    re.compile(r"^\s*(?:\[INFO\]|\[ERROR\])\s*$"),
]

# Prefixes that carry no information once the line is known to be a cause.
# JVM throwable class names end in one of these; `ComparisonFailure` and `AssertionFailedError`
# are as common in real output as `Exception`.
_THROWABLE_SUFFIX = r"(?:Exception|Error|Throwable|Failure)"

_NOISE_PREFIX = re.compile(r"^\s*(?:\[(?:ERROR|FATAL|WARN)\]\s*)+")
_EXCEPTION_PREFIX = re.compile(rf"^(?:Caused by:\s*)?(?:[\w$]+\.)+[\w$]*{_THROWABLE_SUFFIX}(?:\s*:\s*|\s*$)")
_SENTENCE_END = re.compile(r"\.\s")

# --- per-runner rules -------------------------------------------------------------
# Each returns the cause lines it recognises, most specific runner first. The first rule
# that matches wins; nothing is merged across rules, because a runner that emits one shape
# does not emit another.

_JVM_CAUSED_BY = re.compile(r"^\s*Caused by:\s*(.+)$")
_JVM_EXCEPTION = re.compile(rf"^\s*((?:[\w$]+\.)+[\w$]*{_THROWABLE_SUFFIX}(?::.*)?)$")
_PYTEST_SUMMARY = re.compile(r"^FAILED\s+\S+\s+-\s+(.+)$")
_PYTEST_ERROR_LINE = re.compile(r"^\s*E\s{2,}(.+)$")
_JEST_EXPECTATION = re.compile(r"^\s*(expect\(.+\)\..+|(?:Expected|Received)(?:\s+\w+)?:\s*.+)$")
_GO_FAILURE = re.compile(r"^\s*\S+\.go:\d+:\s*(.+)$")
_RUST_PANIC = re.compile(r"panicked at\s+'?(.+?)'?\s*$")
_COMPILER_POSITION = re.compile(r"^\s*(?:\[ERROR\]\s*)?(\S+?):[\[(](\d+)[,:](\d+)[\])]:?\s*(?:error\s+\w+:\s*)?(.*)$")


def _is_excluded(line: str) -> bool:
    return any(pattern.search(line) for pattern in _EXCLUDED_LINES)


def _scrub(text: str, scrubbers: list[tuple[re.Pattern, str]]) -> str:
    for pattern, replacement in scrubbers:
        text = pattern.sub(replacement, text)
    return text


def normalize_cause(text: str) -> str:
    """Strip volatile tokens and framing from a cause line, keeping its meaning intact."""
    cleaned = _scrub(text.strip(), _CAUSE_SCRUBBERS)
    cleaned = _NOISE_PREFIX.sub("", cleaned)
    cleaned = _EXCEPTION_PREFIX.sub("", cleaned).strip()

    if len(cleaned) > CAUSE_MAX_CHARS:
        # Prefer a sentence boundary: runners often append advice to the real message.
        boundary = _SENTENCE_END.search(cleaned)
        if boundary and boundary.start() <= CAUSE_MAX_CHARS:
            cleaned = cleaned[: boundary.start()]
        else:
            cleaned = cleaned[:CAUSE_MAX_CHARS].rstrip() + "..."

    return cleaned


def normalize_output(raw_output: str) -> str:
    """Aggressively normalize arbitrary output. Used only where no rule recognised it."""
    return _scrub(raw_output, _FALLBACK_SCRUBBERS)


def _jvm_causes(lines: list[str]) -> list[str]:
    """The innermost ``Caused by:`` wins - the JVM prints the wrapper first, cause last."""
    caused_by = [match.group(1) for line in lines if (match := _JVM_CAUSED_BY.match(line))]
    if caused_by:
        return [caused_by[-1]]

    exceptions = [match.group(1) for line in lines if (match := _JVM_EXCEPTION.match(line))]
    return exceptions[:1]


def _compiler_causes(lines: list[str]) -> list[str]:
    causes = []
    for line in lines:
        match = _COMPILER_POSITION.match(line)
        if match and match.group(4).strip():
            file_name, row, column, message = match.groups()
            causes.append(f"{file_name}:[{row},{column}] {message.strip()}")
    return causes


def _pytest_causes(lines: list[str]) -> list[str]:
    summaries = [match.group(1) for line in lines if (match := _PYTEST_SUMMARY.match(line))]
    if summaries:
        return summaries

    error_lines = [match.group(1) for line in lines if (match := _PYTEST_ERROR_LINE.match(line))]
    return error_lines[:1]


def _jest_causes(lines: list[str]) -> list[str]:
    return [line.strip() for line in lines if _JEST_EXPECTATION.match(line)]


def _go_causes(lines: list[str]) -> list[str]:
    return [match.group(1) for line in lines if (match := _GO_FAILURE.match(line))]


def _rust_causes(lines: list[str]) -> list[str]:
    return [match.group(1) for line in lines if (match := _RUST_PANIC.search(line))]


_RULES = [_jvm_causes, _compiler_causes, _pytest_causes, _jest_causes, _go_causes, _rust_causes]


def extract_causes(raw_output: str) -> list[str]:
    """Extract the lines that say what went wrong, most specific runner rule first.

    Only ever selects and shortens text the runner emitted - never paraphrases it. When no
    rule recognises the output, the highest-priority failure line is used instead, so the
    result is at worst unhelpful and never invented.
    """
    lines = [line for line in raw_output.splitlines() if line.strip() and not _is_excluded(line)]
    if not lines:
        return []

    for rule in _RULES:
        matched = rule(lines)
        if matched:
            causes: list[str] = []
            for candidate in matched:
                cause = normalize_cause(candidate)
                if cause and cause not in causes:
                    causes.append(cause)
                if len(causes) == MAX_CAUSES:
                    break
            if causes:
                return causes

    return _fallback_causes(lines)


def _fallback_causes(lines: list[str]) -> list[str]:
    """No rule matched. Take the first line that looks like a failure, heavily scrubbed."""
    for line in lines:
        if re.search(r"\b(?:error|exception|fail(?:ed|ure)?|panic|assert)\b", line, re.IGNORECASE):
            return [normalize_cause(normalize_output(line))]

    return [normalize_cause(normalize_output(lines[-1]))]


def compute_fingerprint(causes: list[str]) -> Optional[str]:
    """Hash the cause lines into a short stable identity. No causes means no identity."""
    if not causes:
        return None
    return hashlib.sha256("\n".join(causes).encode("utf-8")).hexdigest()[:FINGERPRINT_LENGTH]


def fingerprint_output(raw_output: Optional[str]) -> tuple[Optional[str], list[str]]:
    """Reduce raw test output to ``(fingerprint, causes)``.

    A passing or empty run has no failure identity and yields ``(None, [])``.
    """
    if not raw_output or not raw_output.strip():
        return None, []

    causes = extract_causes(raw_output)
    return compute_fingerprint(causes), causes
