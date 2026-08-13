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

Boilerplate is identified without any framework knowledge: **a line that appears when the tests pass cannot
be what identifies a failure.** One green run settles the question for every line in it, whatever framework
wrote it. Where no green run exists yet, frequency stands in as a fallback - but counted across
functionalities, never across runs, because a fix loop re-runs one failure up to twenty times and by run
count that failure looks exactly as ubiquitous as the build log around it.

Every entry point degrades to "unknown" (``None``) rather than guessing. The signature feeds an advisory
record, so a missing answer costs a little context while a wrong one costs correctness.
"""

import hashlib
import json
import os
import re
from typing import Optional

from plain2code_console import console

# Fallback route to recognising boilerplate, for a project that has not produced a passing run yet: a line
# in most runs of at least this many different functionalities is furniture. Counting functionalities rather
# than runs is what stops one functionality's repeated failure from voting itself into boilerplate - a fix
# loop can run the same failure twenty times, and by run count alone that failure looks perfectly ubiquitous.
MIN_FUNCTIONALITIES_FOR_MATURE_PROFILE = 3

# Fraction of the project's functionalities a line must turn up under to count as boilerplate on that route.
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
    """Which of this project's test output lines are furniture rather than failure.

    The primary test is simple and hard to argue with: **a line that appears when the tests pass cannot be
    what identifies a failure**. Build logs, startup banners, progress chatter and summary counts all show up
    in a green run; an assertion message does not. That single rule covers most of what needs discarding, and
    it does not care which framework produced the text.

    Frequency across runs is only the fallback, for a project that has not managed a passing run yet, and it
    is counted per functionality rather than per run. Counting runs would be actively wrong: a fix loop
    re-runs the same failure up to twenty times, so by run count that failure is indistinguishable from
    boilerplate - which would erase the signature at precisely the moment it is needed.

    Persisted next to the module's memory, but deliberately outside the folder that memory files are read
    from, so it is never fed into a prompt.
    """

    def __init__(
        self,
        line_functionalities: Optional[dict[str, list[int]]] = None,
        passing_lines: Optional[list[str]] = None,
        run_count: int = 0,
        functionalities_seen: Optional[list[str]] = None,
    ):
        # line -> indices into functionalities_seen. Indices rather than names to keep the file compact.
        self.line_functionalities: dict[str, list[int]] = line_functionalities or {}
        self.passing_lines: set[str] = set(passing_lines or [])
        self.run_count = run_count
        self.functionalities_seen: list[str] = functionalities_seen or []

    @classmethod
    def load(cls, memory_folder: str) -> "LineFrequencyProfile":
        profile_path = os.path.join(memory_folder, PROFILE_FILE_NAME)
        if not os.path.exists(profile_path):
            return cls()

        try:
            with open(profile_path, "r", encoding="utf-8") as profile_file:
                content = json.load(profile_file)
            return cls(
                line_functionalities=content.get("line_functionalities", {}),
                passing_lines=content.get("passing_lines", []),
                run_count=content.get("run_count", 0),
                functionalities_seen=content.get("functionalities_seen", []),
            )
        except (json.JSONDecodeError, OSError, AttributeError) as exception:
            console.debug(f"Could not read the failure profile at {profile_path}: {exception}. Starting a new one.")
            return cls()

    def save(self, memory_folder: str) -> None:
        profile_path = os.path.join(memory_folder, PROFILE_FILE_NAME)
        try:
            os.makedirs(memory_folder, exist_ok=True)
            with open(profile_path, "w", encoding="utf-8") as profile_file:
                json.dump(
                    {
                        "run_count": self.run_count,
                        "functionalities_seen": self.functionalities_seen,
                        "line_functionalities": self.line_functionalities,
                        "passing_lines": sorted(self.passing_lines),
                    },
                    profile_file,
                )
        except OSError as exception:
            console.debug(f"Could not write the failure profile to {profile_path}: {exception}.")

    def observe(self, output: str, passed: bool, functionality: Optional[str] = None) -> None:
        """Record one run of the test script, whether it passed, and which functionality it was testing."""
        skeletons = {_hash(_skeleton(line)) for line in normalize_output(output)}
        if not skeletons:
            return

        self.run_count += 1
        functionality_index = self._index_of(functionality)

        for skeleton_hash in skeletons:
            if passed:
                self.passing_lines.add(skeleton_hash)
            if functionality_index is None:
                self.line_functionalities.setdefault(skeleton_hash, [])
                continue
            seen_in = self.line_functionalities.setdefault(skeleton_hash, [])
            if functionality_index not in seen_in:
                seen_in.append(functionality_index)

        self._evict_if_oversized()

    def _index_of(self, functionality: Optional[str]) -> Optional[int]:
        if functionality is None:
            return None
        if functionality not in self.functionalities_seen:
            self.functionalities_seen.append(functionality)
        return self.functionalities_seen.index(functionality)

    def _evict_if_oversized(self) -> None:
        if len(self.line_functionalities) <= MAX_PROFILE_ENTRIES:
            return

        # Lines seen in a passing run are never evicted: each is a standing answer to "is this furniture?".
        # Beyond those, the lines that turned up under the fewest functionalities are the least useful.
        def retention_key(item: tuple[str, list[int]]) -> tuple[int, int]:
            return (1 if item[0] in self.passing_lines else 0, len(item[1]))

        retained = sorted(self.line_functionalities.items(), key=retention_key, reverse=True)[:MAX_PROFILE_ENTRIES]
        self.line_functionalities = dict(retained)
        self.passing_lines = {line for line in self.passing_lines if line in self.line_functionalities}

    @property
    def is_mature(self) -> bool:
        """Whether the profile can yet tell furniture from failure.

        One passing run is enough on its own, because it settles the question for every line it contains.
        Failing runs only help once several different functionalities have contributed them.
        """
        return bool(self.passing_lines) or len(self.functionalities_seen) >= MIN_FUNCTIONALITIES_FOR_MATURE_PROFILE

    def is_boilerplate(self, line: str) -> bool:
        if not self.is_mature:
            return False

        skeleton_hash = _hash(_skeleton(line))
        if skeleton_hash in self.passing_lines:
            return True

        functionality_count = len(self.functionalities_seen)
        if functionality_count < MIN_FUNCTIONALITIES_FOR_MATURE_PROFILE:
            return False

        seen_under = len(self.line_functionalities.get(skeleton_hash, []))
        return seen_under / functionality_count >= BOILERPLATE_FREQUENCY_THRESHOLD

    def distinctive_lines(self, output: str) -> list[str]:
        """The lines of this run that are not project furniture."""
        return [line for line in normalize_output(output) if not self.is_boilerplate(line)]


def compute_exact_signature(output: str, exit_code: int) -> Optional[str]:
    """Identity of a run's text, needing no profile and therefore available from the very first run.

    Two runs whose normalized text matches are the same failure - there is nothing to infer. This answers the
    common and important case directly: a fix loop grinding on one unchanged failure. It cannot report two
    runs as the same when they differ; at worst it reports two as different when only some volatile token no
    masking rule anticipated separates them, which is the harmless direction.

    Gating this behind the boilerplate profile was a mistake. A module's first functionality has no passing run
    to learn boilerplate from, and that is precisely where a fix loop is most likely to grind.
    """
    normalized = normalize_output(output)
    if not normalized:
        return None

    # Sorted, so that a runner which interleaves output differently between runs still lands on one signature.
    return _hash(f"exact:{exit_code}:{'|'.join(sorted(_hash(line) for line in normalized))}")


def compute_distinctive_signature(output: str, exit_code: int, profile: LineFrequencyProfile) -> Optional[str]:
    """Identity of a failure once the project's boilerplate is known, or None while it is not.

    Where the exact signature only recognises a failure that repeats verbatim, this recognises the same
    failure surfacing amid text that has moved on around it. It needs the profile, so it is an addition to the
    exact signature rather than a replacement: callers keep both and treat a match on either as a repeat.
    """
    if not output or not output.strip() or not profile.is_mature:
        return None

    distinctive = profile.distinctive_lines(output)
    if not distinctive:
        return None

    line_hashes = sorted({_hash(line) for line in distinctive})
    return _hash(f"distinctive:{exit_code}:{'|'.join(line_hashes)}")


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
