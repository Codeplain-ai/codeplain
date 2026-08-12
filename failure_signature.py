"""Language- and framework-agnostic fingerprinting of test script output.

Two questions need answering about a failing conformance test run, and they need answering without an LLM
and without knowing which test framework produced the output:

1. Is this the same failure as a previous attempt, or did something change?
2. What was the failure, in a form a reader can actually understand?

The two questions want opposite treatments of the same text, so this module produces two artifacts:

* A **signature** - a short hash used only as an identity/deduplication key. It is computed over the lines
  that are *not* boilerplate for this project, because test output is dominated by lines that are constant
  across every run (build logs, progress chatter, startup banners). Comparing whole outputs does not work:
  measured against a real captured Jest run, an unchanged failure with fresh PIDs and timings scored *less*
  similar than a genuinely different failure, because the handful of lines that mattered were swamped.

* An **excerpt** - the normalized output, deduplicated but otherwise intact, for a human or a model to read.
  Boilerplate is deliberately *not* stripped here: the reader needs the surrounding context that the
  signature has to discard.

Boilerplate is identified without any framework knowledge, by frequency: a line whose digit-blind skeleton
appears in most runs of this project is boilerplate, whatever it happens to say. The profile spans every run
in the project, including passing ones, so that a failure repeated twenty times in one functionality does not
mistake itself for boilerplate.

Every entry point degrades to "unknown" (``None``) rather than guessing. The signature feeds an advisory
record, so a missing answer costs a little context while a wrong one costs correctness.
"""

import hashlib
import json
import os
import re
from typing import Optional

from plain2code_console import console

# A profile needs a few runs before its frequency estimates mean anything. With only two runs, "appears in
# every run" would describe the shared failure itself rather than the project's boilerplate.
MIN_RUNS_FOR_MATURE_PROFILE = 3

# Fraction of runs a line must appear in to count as boilerplate.
BOILERPLATE_FREQUENCY_THRESHOLD = 0.75

# Retained distinct lines. Well past what a real project produces; a backstop against a script that emits
# unique text on every line forever.
MAX_PROFILE_ENTRIES = 4000

# Outputs larger than this are excerpted from the tail only. Some scripts emit megabytes of build logs.
MAX_OUTPUT_CHARS = 2_000_000

EXCERPT_MAX_LINES = 60
EXCERPT_MAX_CHARS = 4000

PROFILE_FILE_NAME = "failure_profile.json"

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

# Source-code gutters embedded in failure output, e.g. "  106 | throw ..." or "> 108 | throw ...".
# The line number shifts whenever the file above it is edited, so it carries no identity.
_SOURCE_GUTTER = re.compile(r"^(\s*>?\s*)\d+(\s*\|)")

# Ordered: earlier patterns claim text before later, greedier ones can.
#
# Bare numbers are deliberately left alone. Masking them would collapse "expected 5 but got 3" and
# "expected 5 but got 4" into the same line, which is exactly the distinction worth keeping. Numbers are
# masked only where their context marks them volatile.
_VOLATILE_PATTERNS = [
    (re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"), "<UUID>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<TIMESTAMP>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}"), "<DATE>"),
    (re.compile(r"\b\d{1,2}:\d{2}:\d{2}(?:[.,]\d+)?\b"), "<TIME>"),
    (re.compile(r"0x[0-9a-fA-F]+"), "<ADDR>"),
    (re.compile(r"@[0-9a-f]{6,}\b"), "<ADDR>"),
    (
        re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:ns|µs|us|ms|s|sec|secs|seconds?|m|min|mins|minutes?|h|hrs?|hours?)\b"),
        "<DURATION>",
    ),
    (re.compile(r"\b(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]):\d+"), "<HOST>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "<IP>"),
    (re.compile(r"(?:/private)?/var/folders/[^\s:'\"]+"), "<TMPDIR>"),
    (re.compile(r"/tmp/[^\s:'\"]+"), "<TMPDIR>"),
    # Home directories differ per machine and per user, and leak a username into stored records.
    (re.compile(r"(?:/Users|/home)/[^/\s]+/"), "~/"),
    (re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+\\", re.IGNORECASE), r"~\\"),
    # Process ids. Bracketed integers of three digits or more are pids far more often than array indices.
    (re.compile(r"\[\d{3,}\]"), "[<PID>]"),
    (re.compile(r":\d+:\d+\b"), ":<LINE>:<COL>"),
    (re.compile(r"\b[0-9a-f]{7,}\b"), "<HASH>"),
]

_DIGITS = re.compile(r"\d+")


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def mask_volatile(line: str) -> str:
    """Replace tokens that vary between runs without the run meaning anything different."""
    line = _ANSI_ESCAPE.sub("", line)
    line = _SOURCE_GUTTER.sub(r"\1<LINE>\2", line)
    for pattern, replacement in _VOLATILE_PATTERNS:
        line = pattern.sub(replacement, line)
    return line.rstrip()


def _skeleton(masked_line: str) -> str:
    """Digit-blind form of a line, used only to recognise boilerplate.

    Kept separate from the masked form because it is far too lossy for content: it merges "expected 5" with
    "expected 6". That is acceptable when the only question is whether a line is project furniture, and it
    catches volatile numbers that no targeted pattern anticipated.
    """
    return _DIGITS.sub("#", masked_line)


def normalize_output(output: str) -> list[str]:
    """Masked, non-empty lines with duplicates removed, in first-occurrence order.

    Deduplication does most of the compression work: test runners repeat an identical failure block once per
    affected test, and retry loops repeat a progress line a number of times that varies between runs. Both
    collapse to a single line here, which is why the excerpt stays readable and why a run that merely retried
    a different number of times does not read as a different run.
    """
    if not output:
        return []

    if len(output) > MAX_OUTPUT_CHARS:
        output = output[-MAX_OUTPUT_CHARS:]

    seen: set[str] = set()
    normalized: list[str] = []
    for raw_line in output.splitlines():
        masked = mask_volatile(raw_line)
        if not masked.strip():
            continue
        if masked in seen:
            continue
        seen.add(masked)
        normalized.append(masked)

    return normalized


class LineFrequencyProfile:
    """How often each distinct line has been seen across this project's test script runs.

    Persisted next to the module's memory, but deliberately outside the folder that memory files are read
    from, so it is never fed into a prompt.
    """

    def __init__(self, line_counts: Optional[dict[str, int]] = None, run_count: int = 0):
        self.line_counts: dict[str, int] = line_counts or {}
        self.run_count = run_count

    @classmethod
    def load(cls, memory_folder: str) -> "LineFrequencyProfile":
        profile_path = os.path.join(memory_folder, PROFILE_FILE_NAME)
        if not os.path.exists(profile_path):
            return cls()

        try:
            with open(profile_path, "r", encoding="utf-8") as profile_file:
                content = json.load(profile_file)
            return cls(line_counts=content.get("line_counts", {}), run_count=content.get("run_count", 0))
        except (json.JSONDecodeError, OSError, AttributeError) as exception:
            console.debug(f"Could not read the failure profile at {profile_path}: {exception}. Starting a new one.")
            return cls()

    def save(self, memory_folder: str) -> None:
        profile_path = os.path.join(memory_folder, PROFILE_FILE_NAME)
        try:
            os.makedirs(memory_folder, exist_ok=True)
            with open(profile_path, "w", encoding="utf-8") as profile_file:
                json.dump({"run_count": self.run_count, "line_counts": self.line_counts}, profile_file)
        except OSError as exception:
            console.debug(f"Could not write the failure profile to {profile_path}: {exception}.")

    def observe(self, output: str) -> None:
        """Record one run. Every run counts, passing ones included.

        Passing runs are what stop a failure that repeats across twenty fix attempts from looking like
        boilerplate: the lines it shares with successful runs are furniture, the ones it does not are signal.
        """
        skeletons = {_hash(_skeleton(line)) for line in normalize_output(output)}
        if not skeletons:
            return

        self.run_count += 1
        for skeleton_hash in skeletons:
            self.line_counts[skeleton_hash] = self.line_counts.get(skeleton_hash, 0) + 1

        self._evict_if_oversized()

    def _evict_if_oversized(self) -> None:
        if len(self.line_counts) <= MAX_PROFILE_ENTRIES:
            return

        # Rare lines are the ones a boilerplate profile has no use for, so they go first.
        retained = sorted(self.line_counts.items(), key=lambda item: item[1], reverse=True)[:MAX_PROFILE_ENTRIES]
        self.line_counts = dict(retained)

    @property
    def is_mature(self) -> bool:
        return self.run_count >= MIN_RUNS_FOR_MATURE_PROFILE

    def is_boilerplate(self, line: str) -> bool:
        if not self.is_mature:
            return False
        frequency = self.line_counts.get(_hash(_skeleton(line)), 0) / self.run_count
        return frequency >= BOILERPLATE_FREQUENCY_THRESHOLD

    def distinctive_lines(self, output: str) -> list[str]:
        """The lines of this run that are not project furniture."""
        return [line for line in normalize_output(output) if not self.is_boilerplate(line)]


def compute_signature(output: str, exit_code: int, profile: LineFrequencyProfile) -> Optional[str]:
    """Identity key for a failure, or None when no honest answer is available.

    None is returned when the profile is too young to tell boilerplate from signal, or when every line of the
    run is boilerplate and there is therefore nothing to identify the failure by. Callers treat None as
    "unknown" and skip the comparison rather than assuming the failure is new or unchanged.
    """
    if not output or not output.strip():
        return None

    if not profile.is_mature:
        return None

    distinctive = profile.distinctive_lines(output)
    if not distinctive:
        return None

    # Sorted, so that a runner which interleaves output differently between runs still lands on one signature.
    line_hashes = sorted({_hash(line) for line in distinctive})
    return _hash(f"{exit_code}:{'|'.join(line_hashes)}")


def build_excerpt(output: str, max_lines: int = EXCERPT_MAX_LINES, max_chars: int = EXCERPT_MAX_CHARS) -> Optional[str]:
    """A readable account of what went wrong, capped, with any truncation stated rather than hidden.

    Boilerplate is left in on purpose. The signature discards it because it obscures identity; a reader needs
    it, because "which tests ran and which of them failed" lives in exactly those lines.
    """
    normalized = normalize_output(output)
    if not normalized:
        return None

    kept = normalized[:max_lines]
    omitted_lines = len(normalized) - len(kept)

    excerpt = "\n".join(kept)
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars]
        # Drop the partial last line rather than presenting a truncated one as if it were complete.
        excerpt = excerpt[: excerpt.rfind("\n")] if "\n" in excerpt else excerpt
        omitted_lines = len(normalized) - excerpt.count("\n") - 1

    if omitted_lines > 0:
        excerpt += f"\n... [{omitted_lines} further lines omitted]"

    return excerpt
