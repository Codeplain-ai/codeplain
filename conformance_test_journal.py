"""A record of what has already been tried while fixing one functionality's conformance tests.

The fixer sees the current test output, the current test code and the current implementation. What it cannot
see is everything that led to them: the changes made in earlier rounds, whether each one moved the failure,
and which hypotheses are already exhausted. Without that it re-tries approaches that have already failed, and
the cumulative diff of the reverted attempts is invisible because only the surviving state remains on disk.

The journal is written mechanically from data the renderer already holds - the diff of each fix, the reason
code saying whether the fix targeted the tests or the implementation, and the failure that prompted it. No
model call is involved in maintaining it.

Failure text is stored once per distinct failure and referenced by signature, so a twenty round loop that
cycles through three failures stores three excerpts rather than twenty. That is also what makes "this is the
failure we already saw in round 4" fall out of the record instead of having to be inferred.

Recognising a repeat therefore has to work from the very first round, and in particular without the project's
boilerplate profile: a module's first functionality has no passing run to learn boilerplate from, and that is
exactly where a fix loop is most likely to grind. So identity comes primarily from the run's own normalized
text, which needs nothing, and the profile only adds the ability to recognise a failure whose surrounding text
has moved on. When the failure has stood unchanged for several rounds the journal says so at the top, because
that single fact is worth more than the list of rows it is derived from.

The journal covers one functionality and is discarded once that functionality's tests pass, at which point
the durable lessons are extracted from it. It is deliberately kept outside the folder memory files are read
from, so that it is fed to a prompt only where it is wanted.
"""

import json
import os
from typing import Any, Optional

import failure_signature
from plain2code_console import console

JOURNAL_SUBFOLDER = "conformance_test_journal"

# Distinct failures whose text is retained. A fix loop that produces more distinct failures than this is
# thrashing, and the oldest ones have stopped being informative.
MAX_DISTINCT_ISSUES = 8

# Failure excerpts are held for the whole loop and several may be live at once, so they are capped tighter
# than a single excerpt shown on its own would be.
ISSUE_EXCERPT_MAX_LINES = 40
ISSUE_EXCERPT_MAX_CHARS = 2000

# Rounds whose diff is kept in full. Older changes are usually still visible in the current code; what the
# recent diffs uniquely preserve are the intermediate and reverted states that are not.
ROUNDS_WITH_FULL_DIFF = 5
DIFF_MAX_CHARS = 1500

# Consecutive attempts against an unchanged failure before the journal says so in its own right, rather than
# leaving it to be inferred from a long list of rows. Two in a row is noise; three is a pattern.
MIN_REPEATS_TO_REPORT_A_STALL = 3

TARGET_CONFORMANCE_TESTS = "conformance tests"
TARGET_IMPLEMENTATION = "implementation"

PROMPT_FILE_NAME = "conformance_test_journal.md"


def _safe_name(value: str) -> str:
    return "".join(character if character.isalnum() or character in "._-" else "_" for character in value)


class ConformanceTestJournal:
    """Append-only record of the fix rounds for one (module, functionality) pair."""

    def __init__(self, module_name: str, frid: str, attempts: Optional[list] = None, issues: Optional[dict] = None):
        self.module_name = module_name
        self.frid = frid
        self.attempts: list[dict[str, Any]] = attempts or []
        # signature (or a per-round stand-in) -> failure excerpt
        self.issues: dict[str, str] = issues or {}

    @staticmethod
    def journal_path(memory_folder: str, module_name: str, frid: str) -> str:
        return os.path.join(memory_folder, JOURNAL_SUBFOLDER, _safe_name(module_name), f"{_safe_name(frid)}.json")

    @classmethod
    def load(cls, memory_folder: str, module_name: str, frid: str) -> "ConformanceTestJournal":
        path = cls.journal_path(memory_folder, module_name, frid)
        if not os.path.exists(path):
            return cls(module_name, frid)

        try:
            with open(path, "r", encoding="utf-8") as journal_file:
                content = json.load(journal_file)
            return cls(
                module_name=content.get("module", module_name),
                frid=content.get("frid", frid),
                attempts=content.get("attempts", []),
                issues=content.get("issues", {}),
            )
        except (json.JSONDecodeError, OSError, AttributeError) as exception:
            console.debug(f"Could not read the conformance test journal at {path}: {exception}. Starting a new one.")
            return cls(module_name, frid)

    def save(self, memory_folder: str) -> None:
        path = self.journal_path(memory_folder, self.module_name, self.frid)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as journal_file:
                json.dump(
                    {"module": self.module_name, "frid": self.frid, "issues": self.issues, "attempts": self.attempts},
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

    def record_attempt(
        self,
        target: str,
        files_changed: list[str],
        diff_text: Optional[str] = None,
        issue_signature: Optional[str] = None,
        issue_excerpt: Optional[str] = None,
        distinctive_signature: Optional[str] = None,
    ) -> None:
        """Record one fix round: the failure that prompted it and the change it made in response.

        Two identities are kept for the failure. The exact one is always available and recognises a failure
        that recurs verbatim. The distinctive one appears only once the project's boilerplate is known and
        recognises the same failure amid text that has changed around it. A match on either makes it a repeat.
        """
        round_number = len(self.attempts) + 1

        # Only when the run produced no usable output at all is there no identity; file it under a key that
        # cannot collide, so its text survives without being mistaken for a repeat of anything.
        issue_key = issue_signature or f"round-{round_number}"
        if issue_excerpt:
            self.issues.setdefault(issue_key, issue_excerpt)

        self.attempts.append(
            {
                "round": round_number,
                "target": target,
                "files_changed": sorted(files_changed),
                "diff": diff_text[:DIFF_MAX_CHARS] if diff_text else None,
                "issue": issue_key,
                "distinctive_issue": distinctive_signature,
            }
        )

        self._drop_diffs_from_older_rounds()
        self._evict_surplus_issues()

    def _drop_diffs_from_older_rounds(self) -> None:
        for attempt in self.attempts[:-ROUNDS_WITH_FULL_DIFF]:
            attempt["diff"] = None

    def _evict_surplus_issues(self) -> None:
        if len(self.issues) <= MAX_DISTINCT_ISSUES:
            return

        most_recent_use: dict[str, int] = {}
        first_use: dict[str, int] = {}
        for attempt in self.attempts:
            key = attempt.get("issue")
            if key:
                most_recent_use[key] = attempt["round"]
                first_use.setdefault(key, attempt["round"])

        # The failures still in play are the recently referenced ones, but the one the loop started on is
        # what says where it went wrong, so it is kept whatever its age.
        by_recency = sorted(self.issues, key=lambda key: most_recent_use.get(key, 0), reverse=True)
        earliest = min(self.issues, key=lambda key: first_use.get(key, 0))
        retained = list(dict.fromkeys([earliest] + by_recency))[:MAX_DISTINCT_ISSUES]
        self.issues = {key: self.issues[key] for key in retained}

    @staticmethod
    def _same_failure(one: dict, other: dict) -> bool:
        """Whether two attempts were prompted by the same failure.

        Either identity is enough. The exact one catches a failure recurring verbatim; the distinctive one
        catches it recurring amid text that has moved on. A stand-in key minted for a round with no usable
        output starts with "round-" and matches nothing, itself included.
        """
        one_key, other_key = one.get("issue"), other.get("issue")
        if one_key and other_key and one_key == other_key and not str(one_key).startswith("round-"):
            return True

        one_distinctive, other_distinctive = one.get("distinctive_issue"), other.get("distinctive_issue")
        return bool(one_distinctive) and one_distinctive == other_distinctive

    def first_round_with_same_failure(self, attempt: dict) -> Optional[int]:
        """The earliest round prompted by the same failure as this one, if any earlier round was."""
        for earlier in self.attempts:
            if earlier["round"] >= attempt["round"]:
                break
            if self._same_failure(earlier, attempt):
                return int(earlier["round"])
        return None

    def unbroken_repeat_run(self) -> tuple[Optional[int], int]:
        """How long the failure has stood unchanged: (round it was first seen, consecutive attempts since).

        Measured from the most recent attempt backwards, because what matters is whether the loop is stuck
        now, not whether it was stuck earlier and then moved on.
        """
        if not self.attempts:
            return None, 0

        latest = self.attempts[-1]
        consecutive = 0
        for attempt in reversed(self.attempts):
            if not self._same_failure(attempt, latest) and attempt is not latest:
                break
            consecutive += 1

        first_seen = self.first_round_with_same_failure(latest)
        return (first_seen if first_seen is not None else latest["round"]), consecutive

    def render_for_prompt(self) -> Optional[str]:
        """The journal as prose, or None when there is nothing worth saying yet."""
        if not self.attempts:
            return None

        lines = [
            f"# Previous attempts at fixing the conformance tests for functionality {self.frid} "
            f"in module {self.module_name}",
            "",
            "These attempts have already been made. Approaches recorded here as having failed should not be "
            "repeated unless there is a specific reason to believe the circumstances have changed.",
            "",
        ]

        lines.extend(self._render_stall_warning())

        for attempt in self.attempts:
            round_number = attempt["round"]
            lines.append(f"## Attempt {round_number}: changed the {attempt['target']}")

            if attempt["files_changed"]:
                lines.append(f"Files changed: {', '.join(attempt['files_changed'])}")
            else:
                lines.append("No files were changed.")

            repeated_from = self.first_round_with_same_failure(attempt)
            if repeated_from is not None:
                lines.append(
                    f"This attempt was prompted by the same failure as attempt {repeated_from}. Everything "
                    f"changed in between left that failure exactly as it was."
                )

            excerpt = self.issues.get(attempt.get("issue") or "")
            if repeated_from is None:
                if excerpt:
                    lines.append("")
                    lines.append("The failure that prompted this attempt:")
                    lines.append("```")
                    lines.append(excerpt)
                    lines.append("```")
                else:
                    # Say so rather than render a bare row, which reads as though nothing had failed.
                    lines.append("The text of the failure that prompted this attempt is no longer retained.")

            if attempt.get("diff"):
                lines.append("")
                lines.append("The change made:")
                lines.append("```")
                lines.append(attempt["diff"])
                lines.append("```")

            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    def _render_stall_warning(self) -> list[str]:
        """The one thing a stuck loop most needs told, placed where it cannot be missed."""
        first_seen, consecutive = self.unbroken_repeat_run()
        if consecutive < MIN_REPEATS_TO_REPORT_A_STALL or first_seen is None:
            return []

        return [
            f"## The failure has not changed for {consecutive} attempts",
            "",
            f"Every attempt from {first_seen} onwards has been prompted by the same failure, unchanged. None of "
            "the changes made across those attempts affected it at all.",
            "",
            "Continuing to vary the same code is therefore unlikely to help. Whatever is failing has not yet "
            "been reached by any of these changes: consider whether the failure is happening before the code "
            "under test runs at all, whether the tests are exercising what they are assumed to exercise, and "
            "whether the specification requires what the tests assert.",
            "",
        ]


def build_issue_excerpt(output: str) -> Optional[str]:
    return failure_signature.build_excerpt(output, max_lines=ISSUE_EXCERPT_MAX_LINES, max_chars=ISSUE_EXCERPT_MAX_CHARS)
