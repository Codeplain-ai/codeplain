"""Retrieval over evidential memory records.

Records are retrieved along two independent axes, because they answer different questions.

**Loop history** is every attempt already made against the functionality currently being
fixed, identified by ``(testing_module, testing_frid, suite)``. It is not a similarity
match and is not subject to the relevance budget: a fixer that cannot see what the
previous attempts changed has no way to narrow the search space, and will re-apply a
change that was already refuted or oscillate between two changes indefinitely. Loop
history is returned as a chronology, oldest attempt first, so that oscillation is legible
as a sequence rather than as an unordered set of failures.

**Associative evidence** is everything else in the render that resembles the current
failure. It is ranked and budgeted, and queried along two views, following the multi-view
indexing idea without the cost of embeddings:

* the **symbolic** view matches on recorded facts - same failure fingerprint, same test,
  overlapping files - and is the strongest signal for test failures, because an identical
  fingerprint is an exact match rather than a similarity guess
* the **lexical** view scores normalized failure signatures with BM25, which is what
  surfaces near-miss failures the symbolic view cannot see

Retrieval depth for the associative axis adapts to how stuck the render is. Unlike a
conversational agent we do not have to infer intent: the render already knows the current
failure, the functionality being fixed, and how many attempts have been made, so no
planner call is needed.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional

from memory_management.record import RECORD_KIND_FIX_LOOP_SUMMARY, MemoryRecord, Status

# Retrieval depth by number of fix attempts already made. A first pass retrieves nothing:
# there is no failure to match against yet, and injecting memory speculatively is how
# context gets wasted.
_DEPTH_BY_ATTEMPTS = ((0, 0), (2, 3), (5, 6))
_MAX_DEPTH = 12

# Records from a different test surface rank below every same-surface match. A unit-test
# failure and a conformance failure never share a fingerprint (different runners, different
# output), but they can share the files an intervention touched, which is worth surfacing
# once the same-surface evidence is exhausted.
_CROSS_SUITE_PENALTY = 10

# Loop history is bounded by the fix-attempt limit, so it is normally listed in full.
# This is a backstop against a pathological loop, not a relevance budget; when it bites,
# the most recent attempts are kept and the summary reports what was left out.
MAX_LOOP_HISTORY = 40

# Labels for a failure state in the chain. A run that passed has no fingerprint, and
# neither does a failure that produced no recognizable failure lines.
STATE_RESOLVED = "resolved"
STATE_UNKNOWN = "unknown"

# How many entries of the changed-file tally to report. The per-record file lists are
# authoritative; the tally only exists to make repeated rewrites of one file visible.
_MAX_FILE_TALLY = 15

_BM25_K1 = 1.5
_BM25_B = 0.75
_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+")


class MemoryMode(str, Enum):
    """Which records may be retrieved. Exposed as ``--memory-mode`` for benchmarking.

    ``LOOP`` narrows the retrieval axes - loop history only, no associative evidence.
    ``VERIFIED`` and ``REFUTED`` narrow the admissible statuses instead, on both axes.
    """

    OFF = "off"
    LOOP = "loop"
    VERIFIED = "verified"
    REFUTED = "refuted"
    ALL = "all"


def retrieval_depth(fix_attempts: int) -> int:
    """How many records to retrieve, scaled to how stuck the current fix loop is."""
    for threshold, depth in _DEPTH_BY_ATTEMPTS:
        if fix_attempts <= threshold:
            return depth
    return _MAX_DEPTH


def _allowed_statuses(mode: MemoryMode) -> set[str]:
    if mode is MemoryMode.OFF:
        return set()
    if mode is MemoryMode.VERIFIED:
        return {Status.VERIFIED.value}
    if mode is MemoryMode.REFUTED:
        return {Status.REFUTED.value}
    return {Status.VERIFIED.value, Status.REFUTED.value}


@dataclass
class RetrievalResult:
    """What one query returned, kept separated by axis because the two are read differently."""

    # Attempts already made against the functionality being fixed, oldest first.
    loop_history: list[MemoryRecord] = field(default_factory=list)
    # Ranked evidence from elsewhere in the render.
    associative: list[MemoryRecord] = field(default_factory=list)
    # Derived chain over the loop history; ``None`` when there is no history yet.
    loop_summary: Optional[dict] = None


def _tokenize(text: str) -> list[str]:
    return _TOKEN_PATTERN.findall(text.lower())


def _bm25_scores(query: str, records: list[MemoryRecord]) -> dict[str, float]:
    """Score records against the query signature. Corpus is one render, so this is cheap."""
    query_tokens = _tokenize(query)
    if not query_tokens or not records:
        return {}

    documents = {record.memory_id: _tokenize(record.failure.signature) for record in records}
    lengths = [len(tokens) for tokens in documents.values()]
    average_length = sum(lengths) / len(lengths) if lengths else 0.0
    if average_length == 0.0:
        return {}

    document_frequency: Counter = Counter()
    for tokens in documents.values():
        for token in set(tokens):
            document_frequency[token] += 1

    total_documents = len(documents)
    scores: dict[str, float] = {}
    for memory_id, tokens in documents.items():
        if not tokens:
            continue
        term_frequency = Counter(tokens)
        score = 0.0
        for token in query_tokens:
            occurrences = term_frequency.get(token, 0)
            if occurrences == 0:
                continue
            matching_documents = document_frequency[token]
            idf = math.log(1 + (total_documents - matching_documents + 0.5) / (matching_documents + 0.5))
            denominator = occurrences + _BM25_K1 * (1 - _BM25_B + _BM25_B * len(tokens) / average_length)
            score += idf * (occurrences * (_BM25_K1 + 1)) / denominator
        if score > 0.0:
            scores[memory_id] = score

    return scores


def _symbolic_tier(
    record: MemoryRecord,
    fingerprint: Optional[str],
    test_name: Optional[str],
    files_changed: Optional[Iterable[str]],
) -> Optional[int]:
    """Lower tiers are stronger matches. ``None`` means the symbolic view does not match."""
    if fingerprint is not None and record.failure.fingerprint == fingerprint:
        # An exact fingerprint match on a verified intervention is the single most useful
        # record we can surface, so it outranks everything else.
        return 0 if record.status == Status.VERIFIED.value else 1
    if test_name is not None and record.scope.test_name == test_name:
        return 2
    if files_changed:
        if set(files_changed) & set(record.intervention.files_changed):
            return 3
    return None


def rank_records(
    records: list[MemoryRecord],
    fingerprint: Optional[str] = None,
    test_name: Optional[str] = None,
    files_changed: Optional[Iterable[str]] = None,
    signature: Optional[str] = None,
    mode: MemoryMode = MemoryMode.ALL,
    suite: Optional[str] = None,
) -> list[MemoryRecord]:
    """Order records by relevance to the failure currently being worked on."""
    allowed = _allowed_statuses(mode)
    candidates = [record for record in records if record.status in allowed]
    if not candidates:
        return []

    lexical_scores = _bm25_scores(signature, candidates) if signature else {}

    scored: list[tuple[int, float, int, str, MemoryRecord]] = []
    for record in candidates:
        tier = _symbolic_tier(record, fingerprint, test_name, files_changed)
        lexical_score = lexical_scores.get(record.memory_id, 0.0)
        if tier is None:
            if lexical_score <= 0.0:
                continue
            # Lexical-only matches rank behind every symbolic match.
            tier = 4
        if suite is not None and record.scope.suite != suite:
            tier += _CROSS_SUITE_PENALTY
        # More occurrences means the observation repeated within this render, which is
        # independent evidence; ties break toward it.
        scored.append((tier, -lexical_score, -record.occurrences, record.memory_id, record))

    scored.sort(key=lambda item: item[:4])
    return [item[4] for item in scored]


def select_records(
    records: list[MemoryRecord],
    depth: int,
    fingerprint: Optional[str] = None,
    test_name: Optional[str] = None,
    files_changed: Optional[Iterable[str]] = None,
    signature: Optional[str] = None,
    mode: MemoryMode = MemoryMode.ALL,
    suite: Optional[str] = None,
) -> list[MemoryRecord]:
    """Rank, then cut to the adaptive depth."""
    if depth <= 0 or mode is MemoryMode.OFF:
        return []

    ranked = rank_records(
        records,
        fingerprint=fingerprint,
        test_name=test_name,
        files_changed=files_changed,
        signature=signature,
        mode=mode,
        suite=suite,
    )
    return ranked[:depth]


def _state_label(fingerprint: Optional[str], resolved: bool = False) -> str:
    """Name a failure state for the chain. Absence of a fingerprint is itself reportable."""
    if resolved:
        return STATE_RESOLVED
    return fingerprint or STATE_UNKNOWN


def _attempt_order(record: MemoryRecord) -> tuple[int, str, str]:
    """Chronological key. ``observed_at`` breaks ties within one attempt index."""
    return (record.intervention.attempt_index, record.observed_at, record.memory_id)


def is_loop_history(
    record: MemoryRecord,
    testing_module: Optional[str],
    testing_frid: Optional[str],
    suite: Optional[str],
) -> bool:
    """Whether the record describes an attempt against the functionality being fixed now.

    The loop is identified by the functionality under repair and the test surface, not by
    the failure: an attempt that changed the failure into a different one is still part of
    the same loop, and is precisely the attempt a fingerprint match would miss.
    """
    if testing_module is None or testing_frid is None:
        return False

    return (
        record.scope.testing_module == testing_module
        and record.scope.testing_frid == testing_frid
        and (suite is None or record.scope.suite == suite)
    )


def failure_state_sequence(loop_history: list[MemoryRecord]) -> list[str]:
    """The chain of failure states observed across the loop, oldest first.

    Each attempt contributes the state it was applied against; the last attempt also
    contributes the state that followed it, which is the state the loop is in now.
    """
    if not loop_history:
        return []

    sequence = [_state_label(record.failure.fingerprint) for record in loop_history]
    final = loop_history[-1]
    sequence.append(_state_label(final.outcome.fingerprint_after, resolved=final.outcome.exit_code_after == 0))

    return sequence


def revisited_failure_states(sequence: list[str]) -> list[str]:
    """States the loop returned to after having left them - an observed cycle.

    A state repeating on consecutive attempts only means a change had no effect. A state
    reappearing *after a different state intervened* means the loop undid its own progress,
    which is the objective signature of thrashing.
    """
    first_seen: dict[str, int] = {}
    revisited: list[str] = []

    for index, state in enumerate(sequence):
        if state in (STATE_RESOLVED, STATE_UNKNOWN):
            continue
        earlier = first_seen.get(state)
        if earlier is None:
            first_seen[state] = index
            continue
        intervened = any(sequence[between] != state for between in range(earlier + 1, index))
        if intervened and state not in revisited:
            revisited.append(state)

    return revisited


def _files_changed_tally(loop_history: list[MemoryRecord]) -> dict[str, int]:
    """How many attempts changed each file. Repeated rewrites of one file are visible here."""
    tally: Counter = Counter()
    for record in loop_history:
        for file_name in record.intervention.files_changed:
            tally[file_name] += 1

    ordered = sorted(tally.items(), key=lambda item: (-item[1], item[0]))
    return dict(ordered[:_MAX_FILE_TALLY])


def build_loop_summary(
    loop_history: list[MemoryRecord],
    attempts_listed: int,
    testing_module: Optional[str],
    testing_frid: Optional[str],
    suite: Optional[str],
) -> Optional[dict]:
    """Derive the facts about the loop that no single record can show.

    Every value is computed from the records themselves - the chain of failure states, the
    states the loop returned to, and how often each file was rewritten. Nothing here is an
    inference about why, and nothing prescribes what to try next.
    """
    if not loop_history:
        return None

    sequence = failure_state_sequence(loop_history)

    return {
        "kind": RECORD_KIND_FIX_LOOP_SUMMARY,
        "scope": {
            "testing_module": testing_module,
            "testing_frid": testing_frid,
            "suite": suite,
        },
        "attempts_recorded": len(loop_history),
        "attempts_listed": attempts_listed,
        "distinct_failure_states": len({state for state in sequence if state != STATE_RESOLVED}),
        "failure_state_sequence": sequence,
        "revisited_failure_states": revisited_failure_states(sequence),
        "files_changed_across_attempts": _files_changed_tally(loop_history),
    }


def select_memory(
    records: list[MemoryRecord],
    testing_module: Optional[str] = None,
    testing_frid: Optional[str] = None,
    suite: Optional[str] = None,
    fingerprint: Optional[str] = None,
    test_name: Optional[str] = None,
    files_changed: Optional[Iterable[str]] = None,
    signature: Optional[str] = None,
    fix_attempts: int = 0,
    mode: MemoryMode = MemoryMode.ALL,
) -> RetrievalResult:
    """Split the store into loop history and ranked associative evidence.

    The two axes are disjoint by construction: a record belonging to the current loop is
    never also offered as an associative match, so nothing is presented twice.
    """
    if mode is MemoryMode.OFF:
        return RetrievalResult()

    allowed = _allowed_statuses(mode)
    admissible = [record for record in records if record.status in allowed]

    loop_history: list[MemoryRecord] = []
    elsewhere: list[MemoryRecord] = []
    for record in admissible:
        target = loop_history if is_loop_history(record, testing_module, testing_frid, suite) else elsewhere
        target.append(record)

    loop_history.sort(key=_attempt_order)
    summary = build_loop_summary(
        loop_history,
        attempts_listed=min(len(loop_history), MAX_LOOP_HISTORY),
        testing_module=testing_module,
        testing_frid=testing_frid,
        suite=suite,
    )
    # Keep the most recent attempts when the backstop bites: the newest are the ones the
    # next attempt has to avoid repeating. The summary still reports the full chain.
    listed_history = loop_history[-MAX_LOOP_HISTORY:]

    if mode is MemoryMode.LOOP:
        return RetrievalResult(loop_history=listed_history, associative=[], loop_summary=summary)

    associative = select_records(
        elsewhere,
        depth=retrieval_depth(fix_attempts),
        fingerprint=fingerprint,
        test_name=test_name,
        files_changed=files_changed,
        signature=signature,
        mode=mode,
        suite=suite,
    )

    return RetrievalResult(loop_history=listed_history, associative=associative, loop_summary=summary)
