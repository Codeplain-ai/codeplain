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
    ) -> None:
        """Record one fix round: the failure that prompted it and the change it made in response."""
        round_number = len(self.attempts) + 1

        # Without a signature the failure cannot be recognised as one seen before, but its text is still
        # worth keeping, so it is filed under a key that will not collide with another round's.
        issue_key = issue_signature or f"round-{round_number}"
        if issue_excerpt:
            self.issues.setdefault(issue_key, issue_excerpt)

        self.attempts.append(
            {
                "round": round_number,
                "target": target,
                "files_changed": sorted(files_changed),
                "diff": diff_text[:DIFF_MAX_CHARS] if diff_text else None,
                "issue": issue_key if issue_excerpt or issue_key in self.issues else None,
                "issue_identified": issue_signature is not None,
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

        # Keep the failures referenced most recently; those are the ones still in play.
        most_recent_use: dict[str, int] = {}
        for attempt in self.attempts:
            if attempt.get("issue"):
                most_recent_use[attempt["issue"]] = attempt["round"]

        retained = sorted(self.issues, key=lambda key: most_recent_use.get(key, 0), reverse=True)
        self.issues = {key: self.issues[key] for key in retained[:MAX_DISTINCT_ISSUES]}

    def first_round_with_issue(self, issue_key: str, before_round: int) -> Optional[int]:
        """The earliest round that hit this same failure, if an earlier one did."""
        for attempt in self.attempts:
            if attempt["round"] >= before_round:
                break
            if attempt.get("issue") == issue_key and attempt.get("issue_identified"):
                return int(attempt["round"])
        return None

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

        for attempt in self.attempts:
            round_number = attempt["round"]
            lines.append(f"## Attempt {round_number}: changed the {attempt['target']}")

            if attempt["files_changed"]:
                lines.append(f"Files changed: {', '.join(attempt['files_changed'])}")
            else:
                lines.append("No files were changed.")

            issue_key = attempt.get("issue")
            if issue_key:
                repeated_from = self.first_round_with_issue(issue_key, round_number)
                if repeated_from is not None:
                    lines.append(
                        f"The failure that prompted this attempt was the same one already seen in attempt "
                        f"{repeated_from}, so the changes made in between did not resolve it."
                    )
                excerpt = self.issues.get(issue_key)
                if excerpt and repeated_from is None:
                    lines.append("")
                    lines.append("The failure that prompted this attempt:")
                    lines.append("```")
                    lines.append(excerpt)
                    lines.append("```")

            if attempt.get("diff"):
                lines.append("")
                lines.append("The change made:")
                lines.append("```")
                lines.append(attempt["diff"])
                lines.append("```")

            lines.append("")

        return "\n".join(lines).rstrip() + "\n"


def build_issue_excerpt(output: str) -> Optional[str]:
    return failure_signature.build_excerpt(output, max_lines=ISSUE_EXCERPT_MAX_LINES, max_chars=ISSUE_EXCERPT_MAX_CHARS)
