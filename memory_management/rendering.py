"""Rendering of retrieved memory into the block that reaches the prompt.

Records are JSON on disk because that is the right shape for a store: machine-readable,
diffable, auditable. It is the wrong shape for a prompt. Memory sits after the prompt-cache
breakpoint in every template - it changes on every attempt, so it cannot be cached - which
means every token is paid fresh on every call, and on the conformance path the same block
is fed to five sub-prompts per attempt. A record rendered as a JSON document costs on the
order of a thousand tokens to say "pom.xml, 36 lines, resolved it".

So the store renders. Loop history becomes one table with a legend naming each distinct
failure once, instead of repeating a full failure description per record. Observations from
elsewhere become one line each. Diffs are attached only where the diff is the payload: the
last couple of attempts in the current loop, and confirmed observations from elsewhere,
where the change is invisible to the reader because it was made in another module.

This module holds only presentation. It never decides what is relevant - that is
``retrieval`` - and never derives a new fact.
"""

from __future__ import annotations

from typing import Optional

from memory_management.record import MemoryRecord, Status, Transition
from memory_management.retrieval import STATE_RESOLVED, STATE_UNKNOWN, RetrievalResult

# How many of the most recent attempts in the current loop carry their diff. The change
# these made is in the code the reader is looking at, so the diff is a convenience; the
# alternation that produces thrashing involves two changes, so two is the useful depth.
LOOP_DIFF_ATTEMPTS = 2

# What each transition means, in words a reader does not have to decode.
_OUTCOME_WORDS = {
    Transition.RESOLVED.value: "resolved the failure",
    Transition.UNCHANGED.value: "same failure",
    Transition.MUTATED.value: "different failure",
    Transition.REGRESSED.value: "new failure appeared",
}

_TARGET_WORDS = {
    "IMPLEMENTATION": "implementation code",
    "CONFORMANCE_TESTS": "conformance test project",
    "UNCLASSIFIED": "not recorded which",
    "NONE": "nothing",
}


def _state_labels(sequence: list[str]) -> dict[str, str]:
    """Name each distinct failure state once, in the order it first appears."""
    labels: dict[str, str] = {}
    for state in sequence:
        if state in (STATE_RESOLVED, STATE_UNKNOWN) or state in labels:
            continue
        index = len(labels)
        labels[state] = chr(ord("A") + index) if index < 26 else f"S{index + 1}"
    return labels


def _label_of(state: Optional[str], labels: dict[str, str]) -> str:
    if state == STATE_RESOLVED:
        return "passing"
    if state is None or state == STATE_UNKNOWN:
        return "unrecognised"
    return labels.get(state, state)


def _state_column(record: MemoryRecord, labels: dict[str, str]) -> str:
    """The failure this attempt met, and the one it left behind if they differ."""
    before = _label_of(record.failure.fingerprint, labels)
    if record.outcome.exit_code_after == 0:
        return f"{before} -> passing"

    after = _label_of(record.outcome.fingerprint_after, labels)
    return before if after == before else f"{before} -> {after}"


def _diff_block(diff: Optional[str]) -> list[str]:
    return ["```diff", diff.strip(), "```"] if diff else []


def render_loop_history(result: RetrievalResult) -> list[str]:
    """The attempts already made against this functionality, as a chronology."""
    summary = result.loop_summary
    if not result.loop_history or summary is None:
        return []

    scope = summary["scope"]
    sequence = summary["failure_state_sequence"]
    labels = _state_labels(sequence)

    lines = [
        f"## Attempts already made against {scope['testing_module']} / functionality {scope['testing_frid']}",
        "",
        "| # | files changed | lines | outcome | failure state |",
        "| - | ------------- | ----- | ------- | ------------- |",
    ]
    for record in result.loop_history:
        intervention = record.intervention
        files = ", ".join(intervention.files_changed) or "none"
        outcome = _OUTCOME_WORDS.get(record.outcome.transition, record.outcome.transition)
        lines.append(
            f"| {intervention.attempt_index} | {files} | {intervention.lines_changed} "
            f"| {outcome} | {_state_column(record, labels)} |"
        )

    if summary["attempts_listed"] < summary["attempts_recorded"]:
        omitted = summary["attempts_recorded"] - summary["attempts_listed"]
        lines.append("")
        lines.append(f"({omitted} earlier attempt(s) not listed.)")

    if labels:
        lines += ["", "Failure states:"]
        lines += [f"- {label}: {state_causes(result, state)}" for state, label in labels.items()]

    revisited = [_label_of(state, labels) for state in summary["revisited_failure_states"]]
    if revisited:
        lines += [
            "",
            f"Failure state(s) {', '.join(revisited)} were observed again after the failure had moved away "
            "from them, so the changes applied in between cancelled each other out rather than converging.",
        ]

    tally = summary["files_changed_across_attempts"]
    if tally:
        counted = ", ".join(f"{file_name} ({count})" for file_name, count in tally.items())
        lines += ["", f"Attempts touching each file: {counted}"]

    for record in result.loop_history[-LOOP_DIFF_ATTEMPTS:]:
        if not record.intervention.diff:
            continue
        target = _TARGET_WORDS.get(record.intervention.target, record.intervention.target)
        lines += ["", f"Attempt {record.intervention.attempt_index} changed the {target}:"]
        lines += _diff_block(record.intervention.diff)

    return lines


def state_causes(result: RetrievalResult, fingerprint: str) -> str:
    """The cause lines of a failure state, taken from whichever record observed it."""
    for record in result.loop_history:
        if record.failure.fingerprint == fingerprint and record.failure.causes:
            return " / ".join(record.failure.causes)
    return "not recorded"


def _render_observation(record: MemoryRecord, include_diff: bool) -> list[str]:
    """One observation from elsewhere in the render, as a single line plus optional diff."""
    intervention = record.intervention
    files = ", ".join(intervention.files_changed) or "no files"
    causes = " / ".join(record.failure.causes) or "an unrecognised failure"
    verb = "resolved" if record.status == Status.VERIFIED.value else "did not resolve"
    target = _TARGET_WORDS.get(intervention.target, intervention.target)

    line = (
        f"- {files} ({intervention.lines_changed} lines, {target}) {verb} `{causes}` "
        f"in {record.scope.testing_module} / functionality {record.scope.testing_frid} "
        f"[attribution {record.attribution_confidence.lower()}"
    )
    line += f", seen {record.occurrences} times]" if record.occurrences > 1 else "]"

    lines = [line]
    if include_diff:
        lines += _diff_block(intervention.diff)
    return lines


def render_associative(result: RetrievalResult) -> list[str]:
    """Observations from elsewhere in the render, split by what was actually observed."""
    confirmed = [record for record in result.associative if record.status == Status.VERIFIED.value]
    ruled_out = [record for record in result.associative if record.status != Status.VERIFIED.value]

    lines: list[str] = []
    if confirmed:
        lines += ["## Confirmed elsewhere in this render", ""]
        for record in confirmed:
            # The change was made in another module, so the reader cannot see it; without
            # the diff this names a file and stops short of saying what to put in it.
            lines += _render_observation(record, include_diff=True)
    if ruled_out:
        lines += ["", "## Ruled out elsewhere in this render", ""]
        for record in ruled_out:
            lines += _render_observation(record, include_diff=False)

    return lines


def render(result: RetrievalResult) -> str:
    """Render one retrieval into the block that goes into the prompt."""
    sections = [section for section in (render_loop_history(result), render_associative(result)) if section]
    if not sections:
        return ""

    return "\n\n".join("\n".join(section).strip() for section in sections) + "\n"
