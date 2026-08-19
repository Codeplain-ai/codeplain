"""Per-FRID accounting for the two fix loops, and detection of a loop that is stuck.

Both loops — unit tests during implementation, conformance tests afterwards — patch,
re-run the script, and repeat until a budget runs out. Neither noticed when an attempt
changed nothing: benchmark renders spent twenty attempts rewriting one file against one
unchanging assertion before abandoning the render. Two things were missing, and this
module supplies both.

*Detection*: a failure is fingerprinted, and consecutive identical fingerprints for the
same loop and FRID are counted. A streak means the loop is re-patching without effect,
which is the moment worth reporting — not the exhaustion twenty attempts later.

*Measurement*: attempts and failures are counted per (module, FRID, loop), so a render
reports how many iterations convergence took rather than only whether it eventually gave
up. Exhaustion is a rare binary event and a poor basis for comparing configurations;
iterations-to-convergence is close to continuous and says something after a single run.

Recording never affects rendering. These are observations.
"""

import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from plain2code_console import console

UNIT_LOOP = "unit"
CONFORMANCE_LOOP = "conformance"

# Parts of a test script's output that differ between two runs of the very same failure.
# Left in place, any one of them would make every attempt look novel and hide a stuck
# loop; over-normalising would do the reverse and merge failures that differ for real, so
# only demonstrably volatile tokens are erased.
_VOLATILE_PATTERNS = (
    re.compile(r"/tmp/[^\s'\"]+"),  # renderer scratch paths: /tmp/tmpk8flk7f1.script_output
    re.compile(r"\b0x[0-9a-fA-F]+\b"),  # memory addresses
    re.compile(r"\b[0-9a-fA-F]{8,}\b"),  # hashes, uuids, run ids
    re.compile(r"\b\d+(?:\.\d+)?\s*m?s\b"),  # durations: "1335.821531 ms", "22.5s"
    re.compile(r"duration_ms\s+[\d.]+"),
)


def failure_fingerprint(output: str) -> str:
    """A stable identity for one failure, insensitive to run-to-run noise."""
    normalized = output or ""
    for pattern in _VOLATILE_PATTERNS:
        normalized = pattern.sub("", normalized)
    normalized = " ".join(normalized.split())
    return hashlib.sha1(normalized.encode("utf-8", errors="replace")).hexdigest()[:12]


@dataclass
class _LoopCounters:
    attempts: int = 0
    failures: int = 0
    max_repeat: int = 1
    last_fingerprint: Optional[str] = None
    current_repeat: int = 0
    # Failures since the last pass, whether or not they look alike. A loop can fail every
    # single time without ever repeating itself — one benchmark render went 40 for 40 on
    # a functionality whose longest identical run was two — and a streak counter cannot
    # see that at any threshold.
    consecutive_failures: int = 0


@dataclass
class FixLoopMetrics:
    """One per render. Keyed by (module, frid) so a re-rendered FRID keeps accumulating."""

    _counters: Dict[Tuple[str, str], Dict[str, _LoopCounters]] = field(default_factory=dict)
    _order: List[Tuple[str, str]] = field(default_factory=list)

    def record(self, loop: str, module: str, frid: str, passed: bool, output: str) -> Optional[int]:
        """Records one script run. Returns the streak length when this failure is a
        repeat of the one before it in the same loop, otherwise None."""
        key = (module, str(frid))
        if key not in self._counters:
            self._counters[key] = {}
            self._order.append(key)
        counters = self._counters[key].setdefault(loop, _LoopCounters())

        counters.attempts += 1
        if passed:
            counters.last_fingerprint = None
            counters.current_repeat = 0
            counters.consecutive_failures = 0
            return None

        counters.failures += 1
        counters.consecutive_failures += 1
        fingerprint = failure_fingerprint(output)
        if fingerprint == counters.last_fingerprint:
            counters.current_repeat += 1
            counters.max_repeat = max(counters.max_repeat, counters.current_repeat)
            return counters.current_repeat

        counters.last_fingerprint = fingerprint
        counters.current_repeat = 1
        return None

    def current_streak(self, loop: str, module: str, frid: Optional[str]) -> int:
        """How many times in a row this loop has just failed the same way.

        `record` returns the streak as it happens, which is enough to warn but not to
        decide: the fix action runs after the test action and needs to ask the question
        again, from its own call site. A missing frid answers zero rather than raising,
        because nothing was recorded under one either — `report_fix_loop_attempt` skips
        those runs.
        """
        counters = self._counters_for(loop, module, frid)
        return counters.current_repeat if counters else 0

    def consecutive_failures(self, loop: str, module: str, frid: Optional[str]) -> int:
        """How many times in a row this loop has failed, regardless of how it failed."""
        counters = self._counters_for(loop, module, frid)
        return counters.consecutive_failures if counters else 0

    def _counters_for(self, loop: str, module: str, frid: Optional[str]) -> Optional[_LoopCounters]:
        if frid is None:
            return None  # nothing is recorded without one — report_fix_loop_attempt skips those runs
        counters = self._counters.get((module, str(frid)))
        if not counters or loop not in counters:
            return None
        return counters[loop]

    def frid_summary(self, module: str, frid: str) -> Optional[str]:
        """One greppable line per FRID, or None if no script ran for it."""
        counters = self._counters.get((module, str(frid)))
        if not counters:
            return None

        parts = [f"[fix-loop] module={module} frid={frid}"]
        for loop in (UNIT_LOOP, CONFORMANCE_LOOP):
            if loop in counters:
                parts.append(
                    f"{loop}={counters[loop].attempts} "
                    f"{loop}_failed={counters[loop].failures} "
                    f"{loop}_max_repeat={counters[loop].max_repeat}"
                )
        # The per-loop streaks are what a reader needs — the two loops wedge for
        # different reasons and are worth different responses — but the aggregate stays
        # because the benchmark series already collected is indexed on this one number,
        # and dropping it would strand those runs mid-experiment.
        parts.append(f"max_repeat={max(loop.max_repeat for loop in counters.values())}")
        return " ".join(parts)

    def render_summary(self) -> List[str]:
        """Every FRID that ran a test script, in the order it was first reached."""
        summaries = (self.frid_summary(module, frid) for module, frid in self._order)
        return [summary for summary in summaries if summary]


# How many identical failures in a row before the loop is called out. Two can happen when
# a patch legitimately addresses something else first; by three the loop is re-patching
# against a failure it is not moving.
REPEATED_FAILURE_WARNING_THRESHOLD = 3

# How many failures in a row — alike or not — before the loop is called stuck anyway.
# Repetition proves futility quickly but is not necessary for it: a benchmark render
# failed a functionality's conformance tests 40 times out of 40 with a longest identical
# run of two, which no streak threshold can catch. The highest failure count seen on a
# functionality that then recovered is four, so this sits above that with margin.
CONSECUTIVE_FAILURE_THRESHOLD = 6

# Marks the moment a loop stops patching and does something else instead. Greppable on
# purpose, like the other benchmark markers.
STRATEGY_SWITCH_PREFIX = "[strategy-switch]"


def stalled_reason(metrics: "FixLoopMetrics", loop: str, module: str, frid: Optional[str]) -> Optional[str]:
    """Why this loop looks stuck, or None if it still looks like it is working.

    One definition for both loops. The streak arm fires soonest when a loop is
    re-submitting the same fix; the consecutive arm is the catch-all for a loop that
    fails every time while the failures keep changing shape.
    """
    streak = metrics.current_streak(loop, module, frid)
    if streak >= REPEATED_FAILURE_WARNING_THRESHOLD:
        return f"repeated_failure streak={streak}"

    failures = metrics.consecutive_failures(loop, module, frid)
    if failures >= CONSECUTIVE_FAILURE_THRESHOLD:
        return f"no_progress consecutive_failures={failures}"

    return None


def report_fix_loop_attempt(render_context, loop: str, frid: Optional[str], passed: bool, output: str) -> None:
    """Records one script run and tells the user when the loop stops making progress."""
    if frid is None:
        return

    streak = render_context.fix_loop_metrics.record(
        loop, module=render_context.module_name, frid=frid, passed=passed, output=output
    )

    if streak is not None and streak >= REPEATED_FAILURE_WARNING_THRESHOLD:
        console.warning(
            f"The {loop} tests for functionality {frid} have failed the same way {streak} times in a row. "
            f"The last {streak - 1} fix attempts changed nothing that the tests can see."
        )


def report_frid_fix_loop_summary(render_context, frid: Optional[str]) -> None:
    """Emits the per-FRID counts once the FRID is done, successfully or not."""
    if frid is None:
        return

    summary = render_context.fix_loop_metrics.frid_summary(render_context.module_name, frid)
    if summary:
        console.info(summary)
