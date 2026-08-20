"""Ranked retrieval over evidential memory records.

Two views are queried and unioned, following the multi-view indexing idea without the
cost of embeddings:

* the **symbolic** view matches on recorded facts - same failure fingerprint, same test,
  overlapping files - and is the strongest signal for test failures, because an identical
  fingerprint is an exact match rather than a similarity guess
* the **lexical** view scores normalized failure signatures with BM25, which is what
  surfaces near-miss failures the symbolic view cannot see

Retrieval depth adapts to how stuck the render is. Unlike a conversational agent we do
not have to infer intent: the render already knows the current failure and how many fix
attempts have been made, so no planner call is needed.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from enum import Enum
from typing import Iterable, Optional

from memory_management.record import MemoryRecord, Status

# Retrieval depth by number of fix attempts already made. A first pass retrieves nothing:
# there is no failure to match against yet, and injecting memory speculatively is how
# context gets wasted.
_DEPTH_BY_ATTEMPTS = ((0, 0), (2, 3), (5, 6))
_MAX_DEPTH = 12

_BM25_K1 = 1.5
_BM25_B = 0.75
_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+")


class MemoryMode(str, Enum):
    """Which records may be retrieved. Exposed as ``--memory-mode`` for benchmarking."""

    OFF = "off"
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
    )
    return ranked[:depth]
