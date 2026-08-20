"""Schema for evidential memory records.

A memory record is an objective log of a single observation: a test failure that was
observed, the intervention that was applied against it, and the outcome that followed.

Every field is either recorded verbatim from the render or derived deterministically from
recorded fields. Nothing in a record is an inference, and no field is authored by an LLM.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional

SCHEMA_VERSION = 1

# Line-count thresholds for deriving attribution confidence. A small diff that flips a
# failing test to green is strong evidence that the diff caused the change; a large diff
# is weak evidence for any particular part of it.
HIGH_CONFIDENCE_MAX_LINES = 10
MEDIUM_CONFIDENCE_MAX_LINES = 50


class Suite(str, Enum):
    """The test surface that produced the observation."""

    CONFORMANCE = "conformance"
    UNITTEST = "unittest"
    PATCHING = "patching"
    ENVIRONMENT = "environment"


class Transition(str, Enum):
    """How the observed failure changed after the intervention was applied."""

    RESOLVED = "RESOLVED"  # tests pass; the observed failure is gone
    UNCHANGED = "UNCHANGED"  # the same failure fingerprint is still observed
    MUTATED = "MUTATED"  # a different failure is now observed
    REGRESSED = "REGRESSED"  # nothing was failing before, something fails now


class Status(str, Enum):
    """Epistemic status, derived from the transition. Both values are objective."""

    VERIFIED = "VERIFIED"  # the intervention demonstrably resolved the failure
    REFUTED = "REFUTED"  # the intervention demonstrably did not resolve the failure


class InterventionTarget(str, Enum):
    """Which code the intervention changed."""

    IMPLEMENTATION = "IMPLEMENTATION"
    CONFORMANCE_TESTS = "CONFORMANCE_TESTS"
    NONE = "NONE"
    # The changed files are recorded, but which of them are implementation and which
    # are tests is not known. Unit-test fixes land in one response with no such split,
    # and classifying by file path would be a guess rather than an observation.
    UNCLASSIFIED = "UNCLASSIFIED"


class AttributionConfidence(str, Enum):
    """How strongly the outcome can be attributed to the intervention."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Flag(str, Enum):
    """Objective suspicion markers."""

    # The intervention edited conformance tests rather than the implementation. Making a
    # test pass by changing the test is objectively validated but not necessarily correct.
    TEST_FILES_MODIFIED = "test_files_modified"


def derive_transition(
    exit_code_after: int,
    fingerprint_before: Optional[str],
    fingerprint_after: Optional[str],
) -> Transition:
    """Derive the transition purely from observed exit codes and fingerprints."""
    if exit_code_after == 0:
        return Transition.RESOLVED
    if fingerprint_before is None:
        return Transition.REGRESSED
    if fingerprint_after == fingerprint_before:
        return Transition.UNCHANGED
    return Transition.MUTATED


def derive_status(transition: Transition) -> Status:
    """Only a resolved failure counts as a verified intervention."""
    return Status.VERIFIED if transition is Transition.RESOLVED else Status.REFUTED


def derive_attribution_confidence(lines_changed: int) -> AttributionConfidence:
    """Smaller interventions attribute their outcome more strongly."""
    if lines_changed <= HIGH_CONFIDENCE_MAX_LINES:
        return AttributionConfidence.HIGH
    if lines_changed <= MEDIUM_CONFIDENCE_MAX_LINES:
        return AttributionConfidence.MEDIUM
    return AttributionConfidence.LOW


@dataclass
class Scope:
    """Where in the render the observation was made. The symbolic retrieval layer."""

    module: str
    frid: Optional[str]
    testing_module: str
    testing_frid: Optional[str]
    suite: str = Suite.CONFORMANCE.value
    # The addressable test unit that ran: the conformance test folder for conformance
    # runs, the test package for unit tests.
    test_name: Optional[str] = None


@dataclass
class Failure:
    """The failure that was observed before the intervention was applied."""

    fingerprint: Optional[str]
    signature: str
    excerpt: str
    exit_code: int


@dataclass
class Intervention:
    """What was changed, read off the diff. No interpretation of why."""

    attempt_index: int
    target: str
    files_changed: list[str] = field(default_factory=list)
    lines_changed: int = 0
    # ``None`` means not determined, which is different from a determined ``False``.
    touched_implementation: Optional[bool] = None
    touched_test_files: Optional[bool] = None

    def signature(self) -> str:
        """Stable identity of this intervention, used to deduplicate records."""
        return f"{self.target}|{','.join(sorted(self.files_changed))}|{self.lines_changed}"


@dataclass
class Outcome:
    """What was observed after the intervention was applied."""

    exit_code_after: int
    fingerprint_after: Optional[str]
    transition: str


@dataclass
class MemoryRecord:
    """One objective failure -> intervention -> outcome observation."""

    memory_id: str
    scope: Scope
    failure: Failure
    intervention: Intervention
    outcome: Outcome
    status: str
    attribution_confidence: str
    observed_at: str
    render_id: Optional[str] = None
    flags: list[str] = field(default_factory=list)
    occurrences: int = 1
    schema_version: int = SCHEMA_VERSION

    @property
    def file_name(self) -> str:
        return f"{self.memory_id}.json"

    def dedup_key(self) -> tuple[str, str]:
        """Records sharing this key describe the same attempt and are merged."""
        return (self.failure.fingerprint or "", self.intervention.signature())

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=False) + "\n"

    @classmethod
    def from_json(cls, raw: str) -> "MemoryRecord":
        """Parse a record, ignoring unknown keys so older stores stay readable."""
        data: dict[str, Any] = json.loads(raw)
        return cls(
            memory_id=data["memory_id"],
            scope=_build(Scope, data.get("scope", {})),
            failure=_build(Failure, data.get("failure", {})),
            intervention=_build(Intervention, data.get("intervention", {})),
            outcome=_build(Outcome, data.get("outcome", {})),
            status=data["status"],
            attribution_confidence=data["attribution_confidence"],
            observed_at=data["observed_at"],
            render_id=data.get("render_id"),
            flags=list(data.get("flags", [])),
            occurrences=int(data.get("occurrences", 1)),
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
        )


def _build(dataclass_type: Any, data: dict[str, Any]) -> Any:
    """Instantiate a dataclass from a dict, dropping keys the dataclass does not declare."""
    known_fields = {f.name for f in dataclass_type.__dataclass_fields__.values()}
    return dataclass_type(**{key: value for key, value in data.items() if key in known_fields})


def build_memory_id(suite: str, fingerprint: Optional[str], intervention: Intervention) -> str:
    """Build the record's identity from its dedup key.

    The id deliberately encodes exactly what ``MemoryRecord.dedup_key`` compares - the
    failure plus the intervention - and deliberately omits the attempt index. That makes
    the on-disk file name the dedup key, so re-observing the same attempt updates one
    record instead of accumulating near-duplicates.
    """
    intervention_hash = hashlib.sha256(intervention.signature().encode("utf-8")).hexdigest()[:6]
    return f"{suite}-{fingerprint or 'none'}-{intervention_hash}"


def build_record(
    scope: Scope,
    failure: Failure,
    intervention: Intervention,
    exit_code_after: int,
    fingerprint_after: Optional[str],
    observed_at: str,
    render_id: Optional[str] = None,
) -> MemoryRecord:
    """Assemble a record, deriving every derivable field. The single construction path."""
    transition = derive_transition(exit_code_after, failure.fingerprint, fingerprint_after)
    flags = [Flag.TEST_FILES_MODIFIED.value] if intervention.touched_test_files else []

    return MemoryRecord(
        memory_id=build_memory_id(scope.suite, failure.fingerprint, intervention),
        scope=scope,
        failure=failure,
        intervention=intervention,
        outcome=Outcome(
            exit_code_after=exit_code_after,
            fingerprint_after=fingerprint_after,
            transition=transition.value,
        ),
        status=derive_status(transition).value,
        attribution_confidence=derive_attribution_confidence(intervention.lines_changed).value,
        observed_at=observed_at,
        render_id=render_id,
        flags=flags,
    )
