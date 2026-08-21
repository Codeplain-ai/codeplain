"""Per-FRID accounting for the two fix loops, and detection of a stalled loop.

Both loops - unit tests during implementation, conformance tests afterwards - patch,
re-run the test script, and repeat until a budget runs out. Neither detects an attempt
that changed nothing, so a loop can spend its whole budget rewriting the same file
against the same failure.

Detection: each failure is fingerprinted, and consecutive identical fingerprints are
counted per loop and FRID. A streak means the loop is re-patching without effect, which
is worth reporting when it starts rather than when the budget runs out.

Measurement: attempts and failures are counted per (module, FRID, loop), so a render
reports how many iterations convergence took rather than only whether it gave up.
Exhaustion is a rare binary event; iterations-to-convergence is a finer signal and says
something after a single run.

Recording never affects rendering. These are observations.
"""

import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from plain2code_console import console

UNIT_LOOP = "unit"
CONFORMANCE_LOOP = "conformance"

# Parts of a test script's output that differ between two runs of the very same failure.
# Left in place, any one of them would make every attempt look novel and hide a stuck
# loop; over-normalising would do the reverse and merge failures that differ for real, so
# only demonstrably volatile tokens are erased.
_VOLATILE_PATTERNS = (
    # Renderer scratch paths on every supported platform. The directory differs per
    # platform and the name within it per run, so a repeat would otherwise go unrecognised.
    re.compile(r"/tmp/[^\s'\"]+"),  # Linux: /tmp/tmpk8flk7f1.script_output
    re.compile(r"(?:/private)?/var/folders/[^\s'\"]+"),  # macOS: /var/folders/qk/.../T/tmpk8flk7f1
    re.compile(r"[A-Za-z]:\\[^\s'\"]*?\\Temp\\[^\s'\"]*", re.IGNORECASE),  # Windows: C:\...\AppData\Local\Temp\...
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
    # attempt while the failure keeps changing, which a streak counter cannot detect at
    # any threshold.
    consecutive_failures: int = 0


@dataclass
class FixLoopMetrics:
    """One per render. Keyed by (module, frid) so a re-rendered FRID keeps accumulating."""

    _counters: Dict[Tuple[str, str], Dict[str, _LoopCounters]] = field(default_factory=dict)
    _order: List[Tuple[str, str]] = field(default_factory=list)
    _summarized: Set[Tuple[str, str]] = field(default_factory=set)

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

    def start_over(self, loop: str, module: str, frid: Optional[str]) -> None:
        """Clears the stall counters, keeping the cumulative ones.

        Called when the loop is given different work - a regenerated conformance test -
        rather than another patch against the same one. The stall evidence describes a
        test that no longer exists. Keeping it means the replacement's first failure lands
        on a counter already over the threshold, so the replacement is discarded after one
        attempt and the regeneration budget is spent without any replacement being tried.

        Cumulative counts survive: they answer a different question, how much work the
        functionality took, and reporting is indexed on them.
        """
        counters = self._counters_for(loop, module, frid)
        if counters is None:
            return
        counters.consecutive_failures = 0
        counters.current_repeat = 0
        counters.last_fingerprint = None

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
        # Per-loop streaks are what a reader needs, since the two loops stall for
        # different reasons and warrant different responses. The aggregate stays because
        # existing tooling reads it.
        parts.append(f"max_repeat={max(loop.max_repeat for loop in counters.values())}")
        return " ".join(parts)

    def take_frid_summary(self, module: str, frid: str) -> Optional[str]:
        """The summary for this FRID, once. A FRID reports as it finishes, so the sweep at
        the end is for the one that never got there."""
        key = (module, str(frid))
        if key in self._summarized:
            return None
        summary = self.frid_summary(module, frid)
        if summary:
            self._summarized.add(key)
        return summary

    def render_summary(self) -> List[str]:
        """Every FRID that has not already reported, in the order it was first reached."""
        summaries = (self.take_frid_summary(module, frid) for module, frid in self._order)
        return [summary for summary in summaries if summary]


# How many identical failures in a row before the loop is called stalled. Two can happen
# when a patch legitimately addresses something else first; by three the loop is
# re-patching against a failure it is not moving.
REPEATED_FAILURE_WARNING_THRESHOLD = 3

# How many failures in a row - alike or not - before the loop is called stalled anyway.
# A repeated failure proves futility quickly, but a loop can also fail every attempt while
# the failure keeps changing, which no streak threshold detects. Set above the highest
# failure count seen on a functionality that then recovered, so a loop that is slow but
# converging is not cut short.
CONSECUTIVE_FAILURE_THRESHOLD = 6

# Marks the point where a loop stops patching and does something else. Greppable on
# purpose, like the other machine-read markers.
STRATEGY_SWITCH_PREFIX = "[strategy-switch]"


# Which loops treat a run of failures - as opposed to a run of *identical* failures - as
# evidence of a stall. Conformance only.
#
# On the conformance side it catches a loop that fails every attempt while the failure
# keeps changing, which the streak signal cannot see.
#
# On the unit side it produced false positives. A run of differing failures in the unit
# loop is usually the loop working through issues one at a time, and the unit remedy is to
# restart the functionality from scratch, discarding that progress. The threshold was also
# calibrated on conformance recoveries, with no unit-loop equivalent to calibrate against.
#
# Both loops keep the streak signal, where a healthy loop does not repeat a failure.
LOOPS_JUDGED_ON_CONSECUTIVE_FAILURES = (CONFORMANCE_LOOP,)


def stalled_reason(metrics: "FixLoopMetrics", loop: str, module: str, frid: Optional[str]) -> Optional[str]:
    """Why this loop looks stalled, or None if it still looks like it is working.

    The repeated-failure signal applies to both loops: a loop re-submitting the same fix
    is stalled whichever loop it is. The consecutive-failure signal applies only where
    failing every attempt has been shown to mean stalled rather than busy.
    """
    streak = metrics.current_streak(loop, module, frid)
    if streak >= REPEATED_FAILURE_WARNING_THRESHOLD:
        return f"repeated_failure streak={streak}"

    if loop in LOOPS_JUDGED_ON_CONSECUTIVE_FAILURES:
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

    summary = render_context.fix_loop_metrics.take_frid_summary(render_context.module_name, frid)
    if summary:
        console.info(summary)
