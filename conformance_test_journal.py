"""A record of what has already been tried while fixing one functionality's tests.

The fixer sees the current test output, the current test code and the current implementation. What it cannot
see is everything that led to them: the changes made in earlier rounds, whether each one moved the failure,
and which hypotheses are already exhausted. Without that it re-tries approaches that have already failed, and
the cumulative diff of the reverted attempts is invisible because only the surviving state remains on disk.

The journal holds two kinds of note, because "have we seen this failure before?" and "have we tried this fix
before?" are different questions over different evidence:

* A **failure note** - one observed test failure. Recorded once per distinct failure and referenced by every
  round it prompted, so a twenty round loop cycling three failures stores three notes rather than twenty.
* An **attempt note** - one change that was applied and run. Rejected proposals are not recorded; an attempt
  note is always something that actually reached disk.

Each note follows the same four-part shape:

* ``k`` - keys. Computed mechanically from the output or the diff, so they are a pure function of what
  happened. Identity has to be deterministic: two byte-identical runs must key alike, and a key must be
  recomputable under a later comparison rule. Nothing here is model-generated.
* ``g`` - tags. Supplied by the model that reviewed the round, drawn from closed vocabularies so that two
  notes can be compared without an embedding index.
* ``x`` - content. What a reader needs: the failure in the model's words plus the lines it identified as the
  failure, or the diff plus why it was made.
* ``l`` - links. Which other notes this one stands in a relationship to. The load-bearing links (repeats,
  reverts, contradictions) are derived from ``k`` by set arithmetic, never asserted by a model, because a
  hallucinated revert edge would stop a render.

Recognising a repeat has to work from the very first round, and in particular without the project's
boilerplate profile: a module's first functionality has no passing run to learn boilerplate from, and that is
exactly where a fix loop is most likely to grind. So identity comes from the run's own text three ways at
once - verbatim, digit-blind, and by similarity - and a match on any of them is a repeat.

The journal covers one functionality and is discarded once that functionality's tests pass, at which point
the durable lessons are extracted from it. It is deliberately kept outside the folder memory files are read
from, so that it is fed to a prompt only where it is wanted.
"""

import json
import os
import re
from typing import Any, Optional

import failure_signature
from plain2code_console import console

JOURNAL_SUBFOLDER = "conformance_test_journal"

# Bumped whenever the note shape changes. A journal written by an older version is discarded rather than
# migrated: it describes a codebase that has since moved, and half-read history is worse than none.
JOURNAL_VERSION = 3

# Distinct failures whose notes are retained. A loop producing more distinct failures than this is thrashing,
# and the oldest ones have stopped being informative. Notes still in play are never evicted - see
# _evict_surplus_failures.
MAX_FAILURE_NOTES = 12

# Failure evidence is held for the whole loop and several notes may be live at once, so it is capped tighter
# than a single excerpt shown on its own would be.
EVIDENCE_MAX_LINES = 40
EVIDENCE_MAX_CHARS = 2000

# Rounds whose diff is kept in full, plus any round involved in a revert. Older changes are usually still
# visible in the current code; what the recent and reverted diffs uniquely preserve are the intermediate
# states that are not.
ROUNDS_WITH_FULL_DIFF = 5
DIFF_MAX_CHARS_PER_FILE = 1200
DIFF_MAX_CHARS = 3000

# How alike two runs' sketches must be to count as the same failure. Deliberately near-identical: similarity
# over whole outputs is dominated by whatever boilerplate they share, so a conformance failure and a unit
# failure differing in one line out of thirty score 0.94 while being nothing alike. The verbatim and
# digit-blind keys do the real work; this only catches drift that neither of them anticipated. Treating two
# runs as distinct is the harmless direction - it costs a little context, where a wrong merge costs
# correctness.
SAME_FAILURE_SIMILARITY = 0.98

# Similarity at which two *distinct* failures are worth mentioning as possibly related. Advisory only.
RELATED_FAILURE_SIMILARITY = 0.90

# How much of an earlier change a later one must take back to count as undoing it. Containment rather than
# similarity, and measured only in the directions the earlier change actually went: a round that added lines
# and never removed any has nothing to be put back, and a round that removes everything an earlier one added
# has undone it whatever else it did at the same time.
REVERT_CONTAINMENT = 0.80

# Consecutive attempts against an unchanged failure before the journal says so in its own right, rather than
# leaving it to be inferred from a long list of rows. Two in a row is noise; three is a pattern.
MIN_REPEATS_TO_REPORT_A_STALL = 3

# Periods checked when looking for a loop that alternates between failures rather than repeating one. An
# A-B-A-B oscillation never shows an unbroken run, so counting repeats alone cannot see it.
CYCLE_PERIODS = (2, 3)

# Rendered journal size. A monotonically growing block would make the longest, least converging loops the
# most expensive per round, which is the wrong way round.
PROMPT_BYTE_BUDGET = 12000
ATTEMPTS_ALWAYS_RENDERED = 4

LOOP_CONFORMANCE = "conformance"
LOOP_UNIT = "unit"

PHASE_IMPLEMENTATION = "implementation"
PHASE_REFACTORING = "refactoring"
PHASE_INSIDE_CONFORMANCE_FIX = "inside_conformance_fix"

VERDICT_CONFORMANCE_TESTS = "CONFORMANCE_TESTS"
VERDICT_IMPLEMENTATION_CODE = "IMPLEMENTATION_CODE"
VERDICT_CONFLICTING_REQUIREMENTS = "CONFLICTING_REQUIREMENTS"
VERDICT_CONFLICTING_ACCEPTANCE_TESTS = "CONFLICTING_ACCEPTANCE_TESTS"
VERDICT_UNIT_TESTS = "UNIT_TESTS"

ROLE_TEST = "test"
ROLE_IMPLEMENTATION = "impl"

CHANGE_MODIFIED = "modified"
CHANGE_CREATED = "created"
CHANGE_DELETED = "deleted"

PROMPT_FILE_NAME = "conformance_test_journal.md"

# Deliberately loose and language agnostic. Used only to notice that a change removed more assertions than it
# added, which is the shape of a fix that weakens a test rather than repairing one.
_ASSERTION_LINE = re.compile(
    r"\b(assert\w*|expect|expects|should\w*|verify|verifyThat|require|check\w*|must\w*|EXPECT_\w+|ASSERT_\w+)\b",
    re.IGNORECASE,
)

_DELETED_FILE_DIFF = re.compile(r"^File .* was deleted\.$")


def _safe_name(value: str) -> str:
    return "".join(character if character.isalnum() or character in "._-" else "_" for character in value)


def _looks_like_a_test_path(path: str) -> bool:
    lowered = path.lower()
    return "test" in lowered or "spec" in lowered


def describe_change(file_name: str, diff_text: str) -> dict[str, Any]:
    """The shape of one file's change, as keys that can be compared with another change's keys.

    A diff arrives in one of three forms - a unified diff for a file that was edited, the whole content for a
    file that was created, and a sentence for one that was deleted. They are told apart here so that every
    change ends up described the same way, whichever form it arrived in.
    """
    added: list[str] = []
    removed: list[str] = []
    change = CHANGE_MODIFIED

    if not diff_text:
        added_lines: list[str] = []
        removed_lines: list[str] = []
    elif _DELETED_FILE_DIFF.match(diff_text.strip()):
        change = CHANGE_DELETED
        added_lines = []
        removed_lines = []
    elif diff_text.startswith("--- ") or "\n@@" in diff_text or diff_text.startswith("@@"):
        added_lines = []
        removed_lines = []
        for line in diff_text.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                added_lines.append(line[1:])
            elif line.startswith("-"):
                removed_lines.append(line[1:])
    else:
        # No diff markers: the renderer hands back whole content for a file that did not exist before.
        change = CHANGE_CREATED
        added_lines = diff_text.splitlines()
        removed_lines = []

    added = sorted({failure_signature.hash_line(line) for line in added_lines if line.strip()})
    removed = sorted({failure_signature.hash_line(line) for line in removed_lines if line.strip()})

    assertions_added = sum(1 for line in added_lines if _ASSERTION_LINE.search(line))
    assertions_removed = sum(1 for line in removed_lines if _ASSERTION_LINE.search(line))

    return {
        "path": file_name,
        "role": ROLE_TEST if _looks_like_a_test_path(file_name) else ROLE_IMPLEMENTATION,
        "change": change,
        "added": added,
        "removed": removed,
        "assert_delta": assertions_added - assertions_removed,
    }


class ConformanceTestJournal:
    """The notes recorded while fixing one (module, functionality) pair."""

    def __init__(
        self,
        module_name: str,
        frid: str,
        spec_hash: Optional[str] = None,
        failures: Optional[dict] = None,
        attempts: Optional[list] = None,
    ):
        self.module_name = module_name
        self.frid = frid
        self.spec_hash = spec_hash
        self.failures: dict[str, dict[str, Any]] = failures or {}
        self.attempts: list[dict[str, Any]] = attempts or []

    # ========== persistence ==========

    @staticmethod
    def journal_path(memory_folder: str, module_name: str, frid: str) -> str:
        return os.path.join(memory_folder, JOURNAL_SUBFOLDER, _safe_name(module_name), f"{_safe_name(frid)}.json")

    @classmethod
    def load(
        cls, memory_folder: str, module_name: str, frid: str, spec_hash: Optional[str] = None
    ) -> "ConformanceTestJournal":
        """The journal for this functionality, or an empty one.

        A journal is discarded rather than read when it was written by an earlier version of this format, or
        when the functionality's specification has changed since. The second case matters: the journal
        survives a failed render, and after the user has edited the specification in response to that failure
        every note in it is advice about a contract that no longer exists.
        """
        path = cls.journal_path(memory_folder, module_name, frid)
        if not os.path.exists(path):
            return cls(module_name, frid, spec_hash)

        try:
            with open(path, "r", encoding="utf-8") as journal_file:
                content = json.load(journal_file)
        except (json.JSONDecodeError, OSError, AttributeError) as exception:
            console.debug(f"Could not read the conformance test journal at {path}: {exception}. Starting a new one.")
            return cls(module_name, frid, spec_hash)

        if content.get("version") != JOURNAL_VERSION:
            console.debug(f"The conformance test journal at {path} predates the current format. Starting a new one.")
            return cls(module_name, frid, spec_hash)

        recorded_spec_hash = content.get("spec_hash")
        if spec_hash is not None and recorded_spec_hash is not None and recorded_spec_hash != spec_hash:
            console.debug(
                f"The specification of functionality {frid} has changed since the conformance test journal at "
                f"{path} was written. Starting a new one."
            )
            return cls(module_name, frid, spec_hash)

        return cls(
            module_name=content.get("module", module_name),
            frid=content.get("frid", frid),
            spec_hash=recorded_spec_hash or spec_hash,
            failures=content.get("failures", {}),
            attempts=content.get("attempts", []),
        )

    def save(self, memory_folder: str) -> None:
        path = self.journal_path(memory_folder, self.module_name, self.frid)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as journal_file:
                json.dump(
                    {
                        "version": JOURNAL_VERSION,
                        "module": self.module_name,
                        "frid": self.frid,
                        "spec_hash": self.spec_hash,
                        "failures": self.failures,
                        "attempts": self.attempts,
                    },
                    journal_file,
                    indent=2,
                )
        except OSError as exception:
            console.debug(f"Could not write the conformance test journal to {path}: {exception}.")

    def delete(self, memory_folder: str) -> None:
        path = self.journal_path(memory_folder, self.module_name, self.frid)
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError as exception:
            console.debug(f"Could not delete the conformance test journal at {path}: {exception}.")

    # ========== recording ==========

    def record_failure(
        self,
        loop: str,
        exit_code: int,
        exact_signature: Optional[str] = None,
        skeleton_signature: Optional[str] = None,
        sketch: Optional[list[str]] = None,
        distinctive_signature: Optional[str] = None,
        tags: Optional[dict] = None,
        statement: Optional[str] = None,
        evidence: Optional[str] = None,
        canonical_fingerprint: Optional[str] = None,
    ) -> str:
        """Record an observed failure and return its note id, reusing the note if it has been seen before.

        Reusing the id *is* the record that this failure has recurred; there is no separate "same as" link to
        assert or to get wrong. Which rule matched is kept so that a merge can be explained after the fact.
        """
        round_number = len(self.attempts) + 1
        keys = {
            "loop": loop,
            "exit_code": exit_code,
            "exact_sig": exact_signature,
            "skeleton_sig": skeleton_signature,
            "distinctive_sig": distinctive_signature,
            "sketch": sketch or [],
            "fingerprint_sig": failure_signature.hash_text(canonical_fingerprint) if canonical_fingerprint else None,
        }

        existing_id, matched_by = self._find_matching_failure(keys)
        if existing_id is not None:
            note = self.failures[existing_id]
            note["last_round"] = round_number
            note["seen_count"] = note.get("seen_count", 1) + 1
            note["matched_by"] = matched_by
            # A later observation may carry a description where an earlier one had none.
            if statement and not note["x"].get("statement"):
                note["x"]["statement"] = statement
            if evidence and not note["x"].get("evidence"):
                note["x"]["evidence"] = evidence
            if tags and not note.get("g"):
                note["g"] = tags
            return existing_id

        note_id = f"F{len(self.failures) + 1}"
        self.failures[note_id] = {
            "k": keys,
            "g": tags or {},
            "x": {"statement": statement, "evidence": self._cap_evidence(evidence)},
            "l": {"same_root_cause_as": []},
            "first_round": round_number,
            "last_round": round_number,
            "seen_count": 1,
            "matched_by": None,
        }
        self._link_failure_by_fingerprint(note_id)
        return note_id

    def record_attempt(
        self,
        loop: str,
        verdict: str,
        code_diff_files_content: Optional[dict] = None,
        prompted_by: Optional[str] = None,
        phase_context: Optional[str] = None,
        default_role: Optional[str] = None,
        tags: Optional[dict] = None,
        rationale: Optional[str] = None,
    ) -> str:
        """Record one applied change and return its note id.

        Recorded after the change rather than before it, because only then is it known what it touched. A
        round that changed nothing is still recorded: knowing a barren approach was taken is what stops it
        being taken again.
        """
        round_number = len(self.attempts) + 1
        note_id = f"A{round_number}"

        changes = [describe_change(file_name, diff) for file_name, diff in (code_diff_files_content or {}).items()]
        if default_role:
            for change in changes:
                change["role"] = default_role

        keys = {
            "verdict": verdict,
            "fix_signature": self._fix_signature(changes),
            "files": changes,
        }

        note = {
            "id": note_id,
            "round": round_number,
            "loop": loop,
            "phase_context": phase_context,
            "k": keys,
            "g": tags or {},
            "x": {
                "diff": self._cap_diff(code_diff_files_content or {}),
                "rationale": rationale,
            },
            "l": {
                "prompted_by": prompted_by,
                "reverts": None,
                "contradicts": None,
                "same_approach_as": [],
                "outcome_observed_in": None,
            },
        }

        self.attempts.append(note)
        self._link_attempt(note)
        self._record_outcome_of_previous_attempt(note)
        self._drop_diffs_from_older_rounds()
        self._evict_surplus_failures()
        return note_id

    # ========== identity ==========

    def _find_matching_failure(self, keys: dict) -> tuple[Optional[str], Optional[str]]:
        """The note recording this same failure, if one exists, and which rule recognised it.

        Three routes, tried cheapest first. Any one of them is enough: the exact key recognises a failure
        that recurs verbatim, the digit-blind key recognises one whose only difference is a number nothing
        anticipated, and the sketch recognises one surfacing amid text that has moved on around it.
        """
        for note_id, note in self.failures.items():
            recorded = note["k"]
            # Different suites produce different failures by definition, however alike their text.
            if recorded.get("loop") != keys.get("loop"):
                continue
            if recorded.get("exit_code") != keys.get("exit_code"):
                continue

            if keys.get("exact_sig") and recorded.get("exact_sig") == keys["exact_sig"]:
                return note_id, "exact"
            if keys.get("skeleton_sig") and recorded.get("skeleton_sig") == keys["skeleton_sig"]:
                return note_id, "skeleton"
            if keys.get("distinctive_sig") and recorded.get("distinctive_sig") == keys["distinctive_sig"]:
                return note_id, "distinctive"

            similarity = failure_signature.sketch_similarity(recorded.get("sketch") or [], keys.get("sketch") or [])
            if similarity >= SAME_FAILURE_SIMILARITY:
                return note_id, f"similarity:{similarity:.2f}"

        return None, None

    def _link_failure_by_fingerprint(self, note_id: str) -> None:
        """Link failures the model gave the same canonical description, as an advisory relationship only.

        A model's phrasing is not guaranteed stable between rounds, so this is never allowed to make two
        failures the same failure - it only lets the journal say they look related.
        """
        fingerprint = self.failures[note_id]["k"].get("fingerprint_sig")
        if not fingerprint:
            return

        for other_id, other in self.failures.items():
            if other_id == note_id:
                continue
            if other["k"].get("fingerprint_sig") == fingerprint:
                self.failures[note_id]["l"]["same_root_cause_as"].append(other_id)
                other["l"].setdefault("same_root_cause_as", []).append(note_id)

    @staticmethod
    def _fix_signature(changes: list[dict]) -> Optional[str]:
        if not changes:
            return None

        parts = []
        for change in sorted(changes, key=lambda item: (item.get("role", ""), item["path"])):
            parts.append(
                f"{change.get('role', '')}:{change['path']}:{','.join(change['added'])}:{','.join(change['removed'])}"
            )
        return failure_signature.hash_text("fix:" + "|".join(parts))

    def _link_attempt(self, note: dict) -> None:
        """Derive this attempt's relationships to earlier ones from its keys alone.

        Links are recorded on the later note only. Writing them to both ends looks harmless until the loop
        actually oscillates, at which point every attempt reverts several earlier ones and each write
        overwrites the last, leaving a record of pairs that were never pairs.
        """
        for earlier in reversed(self.attempts[:-1]):
            if self._is_revert_of(note, earlier):
                note["l"]["reverts"] = earlier["id"]
                # A revert across loop boundaries is the two fix loops undoing each other, which neither can
                # see from its own history.
                if earlier["loop"] != note["loop"]:
                    note["l"]["contradicts"] = earlier["id"]
                break

        for earlier in self.attempts[:-1]:
            if self._is_same_approach(note, earlier):
                note["l"]["same_approach_as"].append(earlier["id"])

    @staticmethod
    def _change_key(change: dict) -> tuple[str, str]:
        """What makes two changes changes to the same file.

        The role belongs in the key: a module and the conformance test project that consumes it both have a
        `pom.xml`, a `package.json`, a `go.mod`. Comparing one against the other by name alone would report a
        revert between two files that have nothing to do with each other.
        """
        return change.get("role", ""), change["path"]

    @classmethod
    def _is_revert_of(cls, note: dict, earlier: dict) -> bool:
        """Whether this attempt took back what an earlier one did to the same files.

        Measured as containment in whichever directions the earlier change actually went - its additions
        removed again, its removals put back - and only those. Requiring both directions missed the ordinary
        case entirely: a round that only added lines has an empty removal set, so there is no "put back" to
        measure, and demanding one made every such revert invisible. Containment rather than similarity
        because a round that removes everything an earlier one added has undone it even if it rearranged half
        the file in the same breath, which similarity scores at one half and misses.
        """
        earlier_by_key = {cls._change_key(change): change for change in earlier["k"]["files"]}
        overlapping = [change for change in note["k"]["files"] if cls._change_key(change) in earlier_by_key]
        if not overlapping:
            return False

        measured_any_direction = False
        for change in overlapping:
            other = earlier_by_key[cls._change_key(change)]
            undone = failure_signature.line_set_containment(other["added"], change["removed"])
            restored = failure_signature.line_set_containment(other["removed"], change["added"])
            for measurement in (undone, restored):
                if measurement is None:
                    continue
                measured_any_direction = True
                if measurement < REVERT_CONTAINMENT:
                    return False

        return measured_any_direction

    @classmethod
    def _is_same_approach(cls, note: dict, earlier: dict) -> bool:
        if note["k"]["fix_signature"] and note["k"]["fix_signature"] == earlier["k"]["fix_signature"]:
            return True

        approach = note["g"].get("approach")
        if not approach or approach != earlier["g"].get("approach"):
            return False

        return {cls._change_key(change) for change in note["k"]["files"]} == {
            cls._change_key(change) for change in earlier["k"]["files"]
        }

    def _record_outcome_of_previous_attempt(self, note: dict) -> None:
        """What the previous attempt achieved is only knowable once the next failure has been observed.

        Only within the same loop. A unit-test fix followed by a conformance failure has not "become" that
        failure: the unit tests it was fixing went green, and a different suite then failed for its own
        reasons. Attributing it anyway asserts a causal link that is not there, which is worse than saying
        nothing - the record is only worth carrying if it can be trusted about what each change did.
        """
        if len(self.attempts) < 2:
            return

        previous = self.attempts[-2]
        current_failure = note["l"].get("prompted_by")
        if not current_failure or previous["loop"] != note["loop"]:
            return

        previous["l"]["outcome_observed_in"] = current_failure

    # ========== eviction ==========

    def _drop_diffs_from_older_rounds(self) -> None:
        protected = {attempt["id"] for attempt in self.attempts[-ROUNDS_WITH_FULL_DIFF:]}
        for attempt in self.attempts:
            reverts = attempt["l"].get("reverts")
            if reverts:
                # Both ends: what a revert uniquely preserves is the state that is no longer on disk.
                protected.add(attempt["id"])
                protected.add(reverts)

        for attempt in self.attempts:
            if attempt["id"] not in protected:
                attempt["x"]["diff"] = {}

    def _evict_surplus_failures(self) -> None:
        """Drop failure notes nothing still refers to, newest-referenced kept.

        Notes are evicted whole, together with the rounds that referenced them, so no attempt is ever left
        pointing at a note that is no longer there. A row saying its failure text has been forgotten spends
        tokens telling the reader nothing.
        """
        if len(self.failures) <= MAX_FAILURE_NOTES:
            return

        digest = self.analyze()
        in_play = set(digest["failures_cycling"])
        if digest["current_failure"]:
            in_play.add(digest["current_failure"])

        referenced_at: dict[str, int] = {}
        for attempt in self.attempts:
            prompted_by = attempt["l"].get("prompted_by")
            if prompted_by:
                referenced_at[prompted_by] = attempt["round"]

        earliest = min(self.failures, key=lambda note_id: self.failures[note_id].get("first_round", 0))
        by_recency = sorted(self.failures, key=lambda note_id: referenced_at.get(note_id, 0), reverse=True)
        retained = list(dict.fromkeys(list(in_play) + [earliest] + by_recency))[:MAX_FAILURE_NOTES]

        self.failures = {note_id: self.failures[note_id] for note_id in retained}
        for note in self.failures.values():
            note["l"]["same_root_cause_as"] = [
                other for other in note["l"].get("same_root_cause_as", []) if other in self.failures
            ]

    @staticmethod
    def _cap_evidence(evidence: Optional[str]) -> Optional[str]:
        """Bound the evidence held on a note, keeping the end of it.

        The end, because that is where a failure is: a runner reports its failures after whatever it printed
        getting there. Cutting from the front here undid the anchoring done upstream - the excerpt arrived
        already centred on the failure and this dropped the very line it had been centred on.
        """
        if not evidence:
            return None

        lines = evidence.splitlines()
        capped_lines = lines[-EVIDENCE_MAX_LINES:]
        capped = "\n".join(capped_lines)
        if len(capped) > EVIDENCE_MAX_CHARS:
            capped = capped[-EVIDENCE_MAX_CHARS:]
            capped = capped[capped.find("\n") + 1 :] if "\n" in capped else capped
            capped_lines = capped.splitlines()

        omitted = len(lines) - len(capped_lines)
        if omitted > 0:
            capped = f"... [{omitted} earlier lines omitted]\n" + capped
        return capped

    @staticmethod
    def _cap_diff(code_diff_files_content: dict) -> dict[str, str]:
        capped: dict[str, str] = {}
        remaining = DIFF_MAX_CHARS
        for file_name, diff in code_diff_files_content.items():
            if remaining <= 0:
                capped[file_name] = "... [diff omitted]"
                continue
            allowance = min(DIFF_MAX_CHARS_PER_FILE, remaining)
            text = diff or ""
            if len(text) > allowance:
                text = text[:allowance] + "\n... [truncated]"
            capped[file_name] = text
            remaining -= len(text)
        return capped

    # ========== analysis ==========

    def analyze(self) -> dict[str, Any]:
        """What the shape of this loop says about whether it is getting anywhere."""
        failure_sequence = [attempt["l"].get("prompted_by") for attempt in self.attempts]

        return {
            "rounds": len(self.attempts),
            "rounds_by_loop": {
                loop: sum(1 for attempt in self.attempts if attempt["loop"] == loop)
                for loop in {attempt["loop"] for attempt in self.attempts}
            },
            "current_failure": failure_sequence[-1] if failure_sequence else None,
            "stall_run": self._unbroken_repeat_run(failure_sequence),
            "cycle": self._detect_cycle(failure_sequence),
            "failures_cycling": self._failures_cycling(failure_sequence),
            "revert_pairs": [
                (attempt["l"]["reverts"], attempt["id"]) for attempt in self.attempts if attempt["l"].get("reverts")
            ],
            "contradiction_pairs": [
                (attempt["l"]["contradicts"], attempt["id"])
                for attempt in self.attempts
                if attempt["l"].get("contradicts")
            ],
            "assertions_removed_in_rounds": [
                attempt["round"]
                for attempt in self.attempts
                if any(change.get("assert_delta", 0) < 0 for change in attempt["k"]["files"])
            ],
        }

    @staticmethod
    def _unbroken_repeat_run(failure_sequence: list) -> int:
        """How many attempts in a row the most recent failure has prompted, unchanged."""
        if not failure_sequence or failure_sequence[-1] is None:
            return 0

        latest = failure_sequence[-1]
        consecutive = 0
        for failure_id in reversed(failure_sequence):
            if failure_id != latest:
                break
            consecutive += 1
        return consecutive

    @staticmethod
    def _detect_cycle(failure_sequence: list) -> Optional[dict]:
        """A repeating period in the failures, for a loop that alternates rather than stalling.

        An unbroken run cannot see A-B-A-B: every attempt differs from the one before it, so by that measure
        nothing is repeating, while in fact nothing is progressing either.
        """
        usable = [failure_id for failure_id in failure_sequence if failure_id]
        for period in CYCLE_PERIODS:
            needed = period * 2
            if len(usable) < needed:
                continue
            window = usable[-needed:]
            if window[:period] == window[period:] and len(set(window)) > 1:
                return {"period": period, "failures": window[:period]}
        return None

    @classmethod
    def _failures_cycling(cls, failure_sequence: list) -> list[str]:
        cycle = cls._detect_cycle(failure_sequence)
        if cycle:
            return list(dict.fromkeys(cycle["failures"]))
        if cls._unbroken_repeat_run(failure_sequence) >= MIN_REPEATS_TO_REPORT_A_STALL:
            return [failure_sequence[-1]]
        return []

    # ========== rendering ==========

    def render_for_prompt(self, byte_budget: int = PROMPT_BYTE_BUDGET) -> Optional[str]:
        """The journal as prose, or None when there is nothing worth saying yet.

        The digest comes first and is never truncated: that a loop has stopped converging is worth more than
        any of the rows the conclusion was drawn from. Rows then fill whatever budget is left, chosen for
        relevance to the failure in hand rather than taken in order.
        """
        if not self.attempts:
            return None

        digest = self.analyze()
        lines = [
            f"# Previous attempts at fixing the tests for functionality {self.frid} in module {self.module_name}",
            "",
            "These attempts have already been made. Approaches recorded here as having failed should not be "
            "repeated unless there is a specific reason to believe the circumstances have changed.",
            "",
        ]
        lines.extend(self._render_digest(digest))

        header = "\n".join(lines)
        remaining = max(0, byte_budget - len(header))

        rendered_rows = []
        # A failure's text is written out once however many rounds it prompted. Repeating it is how thirty-five
        # rounds on one failure came to cost thirty-five copies of it.
        failure_shown_at: dict[str, int] = {}
        for attempt in self._attempts_worth_rendering(digest):
            row = self._render_attempt(attempt, failure_shown_at)
            if len(row) > remaining:
                break
            rendered_rows.append(row)
            remaining -= len(row)

        omitted = len(self.attempts) - len(rendered_rows)
        body = "\n".join(rendered_rows)
        if omitted > 0:
            body += f"\n_{omitted} earlier attempts are summarised above but not listed individually._\n"

        return (header + "\n" + body).rstrip() + "\n"

    def render_recent_implementation_changes(self, limit: int = 3) -> Optional[str]:
        """The implementation changes just made to satisfy a conformance test, for the unit-test fixer.

        Without this the unit-test fixer is the least informed actor in the loop: it sees a unit test failing
        against implementation code that was deliberately changed moments earlier, with no indication that the
        change was intentional, and reasonably concludes the implementation is wrong. Restoring what the unit
        test expected then breaks the conformance test again, and the two loops trade the same lines back and
        forth without either being able to see the other's history.
        """
        deliberate = [
            attempt
            for attempt in self.attempts
            if attempt["loop"] == LOOP_CONFORMANCE and attempt["k"]["verdict"] != VERDICT_CONFORMANCE_TESTS
        ]
        if not deliberate:
            return None

        lines = [
            "# Implementation changes made to satisfy the conformance tests",
            "",
            "The changes below were made deliberately, to make a failing conformance test pass. They are the "
            "behaviour the conformance tests require. A unit test that now fails because of one of them is "
            "asserting the behaviour that was there before the change.",
            "",
        ]

        for attempt in deliberate[-limit:]:
            lines.append(
                f"## Change {attempt['round']}: {', '.join(change['path'] for change in attempt['k']['files'])}"
            )
            prompted_by = attempt["l"].get("prompted_by")
            if prompted_by:
                lines.append(f"Made in response to: {self._describe_failure(prompted_by)}")
            if attempt["g"].get("expected_effect"):
                lines.append(f"Intended effect: {attempt['g']['expected_effect']}")
            for file_name, diff in (attempt["x"].get("diff") or {}).items():
                lines.extend([f"`{file_name}`:", "```", diff, "```"])
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    def _attempts_worth_rendering(self, digest: dict) -> list[dict]:
        """The rounds that carry information about the situation now, most recent first."""
        relevant_ids: list[str] = []

        for first, second in digest["contradiction_pairs"] + digest["revert_pairs"]:
            relevant_ids.extend([first, second])

        current_failure = digest["current_failure"]
        if current_failure:
            relevant_ids.extend(
                attempt["id"] for attempt in self.attempts if attempt["l"].get("prompted_by") == current_failure
            )

        relevant_ids.extend(attempt["id"] for attempt in self.attempts[-ATTEMPTS_ALWAYS_RENDERED:])

        by_id = {attempt["id"]: attempt for attempt in self.attempts}
        ordered = sorted(
            {note_id for note_id in relevant_ids if note_id in by_id},
            key=lambda note_id: by_id[note_id]["round"],
            reverse=True,
        )
        return [by_id[note_id] for note_id in ordered]

    def _render_digest(self, digest: dict) -> list[str]:
        lines = [f"## Where this loop stands after {digest['rounds']} attempts", ""]

        by_loop = ", ".join(
            f"{count} while fixing the {loop} tests" for loop, count in digest["rounds_by_loop"].items()
        )
        if by_loop:
            lines.append(f"Attempts so far: {by_loop}.")

        if digest["cycle"]:
            cycling = " and ".join(self._describe_failure(note_id) for note_id in digest["cycle"]["failures"])
            lines.extend(
                [
                    "",
                    f"**The loop is alternating between {len(digest['cycle']['failures'])} failures rather than "
                    f"resolving either.** It is cycling between {cycling}. Each change fixes one and brings the "
                    "other back, so continuing to alternate between them cannot succeed: either one of the two "
                    "expectations is wrong, or the requirements behind them are in conflict.",
                ]
            )
        elif digest["stall_run"] >= MIN_REPEATS_TO_REPORT_A_STALL:
            lines.extend(
                [
                    "",
                    f"**The failure has not changed for {digest['stall_run']} attempts.** None of the changes made "
                    "across those attempts affected it at all. Whatever is failing has not yet been reached by any "
                    "of them: consider whether the failure happens before the code under test runs, whether the "
                    "tests exercise what they are assumed to exercise, and whether the specification requires what "
                    "the tests assert.",
                ]
            )

        contradictions = digest["contradiction_pairs"]
        if contradictions:
            # One statement, however many pairs. Repeating the same paragraph per pair is how a stuck loop's
            # own diagnosis becomes the bulk of its prompt.
            described = ", ".join(
                f"{self._round_of(later)} undid {self._round_of(earlier)}" for earlier, later in contradictions[-3:]
            )
            more = len(contradictions) - len(contradictions[-3:])
            also = f", and {more} earlier pairs did the same" if more > 0 else ""
            lines.extend(
                [
                    "",
                    f"**Changes made while fixing one test suite have been undone while fixing the other "
                    f"{len(contradictions)} times** (attempt {described}{also}). One suite's expectation is being "
                    "satisfied by breaking the other's. This cannot be resolved by changing the same code again: "
                    "the two expectations have to be reconciled, or reported as conflicting requirements.",
                ]
            )

        removed_in = digest["assertions_removed_in_rounds"]
        if removed_in:
            rounds = ", ".join(str(round_number) for round_number in removed_in)
            subject = f"Attempt {rounds} removed" if len(removed_in) == 1 else f"Attempts {rounds} removed"
            lines.extend(
                [
                    "",
                    f"{subject} more assertions than they added. Weakening a test does not make the behaviour it "
                    "checked correct.",
                ]
            )

        lines.append("")
        return lines

    def _round_of(self, attempt_id: Optional[str]) -> str:
        for attempt in self.attempts:
            if attempt["id"] == attempt_id:
                return str(attempt["round"])
        return "?"

    def _describe_failure(self, note_id: Optional[str]) -> str:
        note = self.failures.get(note_id or "")
        if not note:
            return "an unrecorded failure"
        statement = (note["x"].get("statement") or "").strip()
        return statement if statement else f"failure {note_id}"

    def _render_attempt(self, attempt: dict, failure_shown_at: dict[str, int]) -> str:
        loop_description = "unit-test fix" if attempt["loop"] == LOOP_UNIT else "conformance-test fix"
        lines = [f"## Attempt {attempt['round']} ({loop_description}), verdict {attempt['k']['verdict']}"]

        files = attempt["k"]["files"]
        if files:
            lines.append("Files changed: " + ", ".join(f"{change['path']} ({change['role']})" for change in files))
        else:
            lines.append("No files were changed.")

        if attempt["g"].get("approach"):
            lines.append(f"Approach: {attempt['g']['approach']}.")
        if attempt["g"].get("expected_effect"):
            lines.append(f"Expected effect: {attempt['g']['expected_effect']}")
        if attempt["g"].get("risk"):
            lines.append(f"Noted risk: {attempt['g']['risk']}.")

        if attempt["l"].get("reverts"):
            lines.append(f"This attempt took back what attempt {self._round_of(attempt['l']['reverts'])} had done.")
        if attempt["l"].get("same_approach_as"):
            rounds = ", ".join(self._round_of(other) for other in attempt["l"]["same_approach_as"])
            lines.append(f"This is the same approach already taken in attempt(s) {rounds}.")

        prompted_by = attempt["l"].get("prompted_by")
        failure = self.failures.get(prompted_by or "")
        if failure and prompted_by in failure_shown_at:
            lines.append(
                f"Prompted by the same failure, unchanged - its text is shown under attempt "
                f"{failure_shown_at[prompted_by]}. Everything changed in between left it exactly as it was."
            )
        elif failure:
            failure_shown_at[prompted_by] = attempt["round"]
            lines.append("")
            lines.append(f"The failure that prompted it: {self._describe_failure(prompted_by)}")
            if failure["g"].get("failure_phase"):
                lines.append(f"Failure phase: {failure['g']['failure_phase']}.")
            if failure["x"].get("evidence"):
                lines.extend(["```", failure["x"]["evidence"], "```"])

        outcome = attempt["l"].get("outcome_observed_in")
        if outcome:
            if outcome == prompted_by:
                lines.append("After this change the failure was unchanged.")
            else:
                lines.append(f"After this change the failure became: {self._describe_failure(outcome)}")

        if attempt["x"].get("diff"):
            lines.append("")
            lines.append("The change made:")
            for file_name, diff in attempt["x"]["diff"].items():
                lines.extend([f"`{file_name}`:", "```", diff, "```"])

        lines.append("")
        return "\n".join(lines)


def compute_spec_hash(specifications: Any) -> Optional[str]:
    """A key for the specification a journal was written against.

    The journal outlives a failed render, so the next render of the same functionality would otherwise be
    handed notes about approaches taken against a specification the user has since rewritten in response to
    that very failure.
    """
    if not specifications:
        return None
    return failure_signature.hash_text(json.dumps(specifications, sort_keys=True, default=str))


def build_issue_excerpt(output: str) -> Optional[str]:
    """A readable account of a failure, for the rounds where no model described one.

    Anchored on the failure rather than on the start of the output. The unit-test loop has no reviewer to
    describe its rounds, so this is the only evidence a unit-test failure note carries - and taking the first
    lines of the run gave the build banner every time.
    """
    return failure_signature.build_failure_excerpt(output, max_lines=EVIDENCE_MAX_LINES, max_chars=EVIDENCE_MAX_CHARS)
