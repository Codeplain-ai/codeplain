"""Deterministic journal of conformance tests fix attempts.

While a functionality's conformance tests are failing, every fix attempt is recorded here: the
issue that triggered it, what the fixer said it tried, which files it changed and whether the tests
passed afterwards. A digest of the journal is sent back with every fix request so the fixer does
not repeat attempts that already failed, and once the functionality's conformance tests all pass
the journal is distilled into durable memories and deleted.

Each entry carries the issue in two forms: the raw script output (kept untruncated, on disk only)
and the prepared form the server built for the fix prompt (trimmed, and summarized or truncated
when oversized) - the issue exactly the way the fixer saw it. The prepared form is what travels in
the fix digest and the distillation payload; the raw form exists for humans debugging a render.
An attempt's outcome text is not stored twice: the output produced after an attempt is the raw
issue of the next attempt.

Journals are keyed by the (module, frid) whose tests are being fixed - during the regression sweep
that can be a previously implemented functionality, possibly from a required module.
"""

import hashlib
import json
import os
import shutil

from plain2code_console import console

CONFORMANCE_TEST_JOURNAL_SUBFOLDER = "conformance_test_journal"

# Cap for the issue excerpts included in payloads (the per-attempt issue in the fix digest, and the
# initial issue in the distillation payload). The server-prepared issue is already bounded (LLM
# summary or ~10k-char truncation), so this only trims the rare oversized case - it must not
# routinely cut into summaries the server already sized for a prompt.
MAX_ISSUE_EXCERPT_CHARS = 8000

# The keys of a journal entry that make up the digest sent to the fixer and the distiller.
DIGEST_KEYS = ["attempt", "hypothesis", "approach", "target", "files_changed", "duplicate_of", "result"]


def _trim_issue(issue: str | None, max_chars: int) -> str | None:
    if issue is None or len(issue) <= max_chars:
        return issue
    return f"(TRUNCATED - showing the last {max_chars} characters)\n" + issue[-max_chars:]


def normalized_diff_hash(diff_files: dict[str, str] | None) -> str | None:
    """Hashes a fix's diff so that essentially identical retries can be detected.

    Whitespace-only differences are ignored - a fix that reproduces an earlier failed change with
    different formatting is still the same attempt.
    """
    if not diff_files:
        return None

    normalized_parts = []
    for file_name in sorted(diff_files):
        content_lines = [line.strip() for line in (diff_files[file_name] or "").splitlines() if line.strip()]
        normalized_parts.append(file_name + "\n" + "\n".join(content_lines))

    return hashlib.sha256("\n".join(normalized_parts).encode("utf-8")).hexdigest()


class FixAttemptJournal:
    """File-backed journal, one JSONL file per (module, frid) whose conformance tests are fixed."""

    def __init__(self, memory_folder: str):
        self.journal_folder = os.path.join(memory_folder, CONFORMANCE_TEST_JOURNAL_SUBFOLDER)

    def _journal_path(self, module_name: str, frid: str) -> str:
        return os.path.join(self.journal_folder, f"{module_name}__{frid}.jsonl")

    def _read_entries(self, module_name: str, frid: str) -> list[dict]:
        journal_path = self._journal_path(module_name, frid)
        if not os.path.exists(journal_path):
            return []

        entries = []
        with open(journal_path, "r", encoding="utf-8") as journal_file:
            for line in journal_file:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    console.error(f"Skipping malformed journal entry in {journal_path}.")

        return entries

    def _write_entries(self, module_name: str, frid: str, entries: list[dict]):
        os.makedirs(self.journal_folder, exist_ok=True)
        with open(self._journal_path(module_name, frid), "w", encoding="utf-8") as journal_file:
            for entry in entries:
                journal_file.write(json.dumps(entry) + "\n")

    def open_attempt(self, module_name: str, frid: str, attempt_no: int, issue_before: str):
        """Starts a new journal entry for a fix attempt that is about to be made.

        The raw issue is stored untruncated - it never travels in a payload, only its prepared form
        (recorded later by record_fix) does.
        """
        entries = self._read_entries(module_name, frid)
        entries.append({"attempt": attempt_no, "issue_before_raw": issue_before})
        self._write_entries(module_name, frid, entries)

    def record_fix(
        self,
        module_name: str,
        frid: str,
        fix_attempt_summary: dict | None,
        files_changed: list[str],
        target: str,
        diff_files: dict[str, str] | None,
        prepared_issue: str | None,
    ):
        """Completes the open attempt with what the fix actually did.

        The prepared issue is the form the server built for the fix prompt - the issue exactly the
        way the fixer saw it. The diff hash marks the attempt as a duplicate when an earlier attempt
        in the same journal produced essentially the same change - the strongest signal that the
        fixer is going in circles.
        """
        entries = self._read_entries(module_name, frid)
        if not entries:
            console.error("Cannot record a fix - no journal entry is open.")
            return

        entry = entries[-1]
        if isinstance(fix_attempt_summary, dict):
            entry["hypothesis"] = fix_attempt_summary.get("hypothesis")
            entry["approach"] = fix_attempt_summary.get("approach")
        if prepared_issue:
            entry["issue_before"] = prepared_issue
        entry["files_changed"] = sorted(files_changed)
        entry["target"] = target

        diff_hash = normalized_diff_hash(diff_files)
        entry["diff_hash"] = diff_hash
        if diff_hash is not None:
            for earlier_entry in entries[:-1]:
                if earlier_entry.get("diff_hash") == diff_hash:
                    entry["duplicate_of"] = earlier_entry["attempt"]
                    break

        self._write_entries(module_name, frid, entries)

    def record_result(self, module_name: str, frid: str, passed: bool):
        """Records whether the conformance tests passed after the last fix attempt was applied.

        A failed run's output is not stored here - it becomes the raw issue of the next attempt.
        Does nothing when no attempt is pending: the first test run of a functionality has no fix
        attempt behind it.
        """
        entries = self._read_entries(module_name, frid)
        if not entries or "result" in entries[-1]:
            return

        entries[-1]["result"] = "conformance tests passed" if passed else "conformance tests still failed"
        self._write_entries(module_name, frid, entries)

    def build_digest(self, module_name: str, frid: str) -> list[dict]:
        """Builds the compact attempt history sent with a fix request.

        Only attempts whose fix has been recorded are included - the entry just opened for the
        attempt being made (and any dangling entry an interrupted render left behind) carries no
        information for the fixer. Each included attempt carries the prepared issue it addressed,
        so the fixer can see how the issue evolved across attempts. Raw outputs never travel - the
        current issue is sent with the request separately.
        """
        return [self._digest_entry(entry) for entry in self._read_entries(module_name, frid) if "target" in entry]

    @staticmethod
    def _digest_entry(entry: dict) -> dict:
        digest_entry = {key: entry[key] for key in DIGEST_KEYS if key in entry}
        if entry.get("issue_before"):
            digest_entry["issue"] = _trim_issue(entry["issue_before"], MAX_ISSUE_EXCERPT_CHARS)
        return digest_entry

    def collect_all(self) -> list[dict]:
        """Collects the digests of every journal, keyed by the tested module and frid, for distillation."""
        if not os.path.exists(self.journal_folder):
            return []

        journals = []
        for file_name in sorted(os.listdir(self.journal_folder)):
            if not file_name.endswith(".jsonl") or "__" not in file_name:
                continue
            module_name, _, frid = file_name[: -len(".jsonl")].rpartition("__")
            entries = [entry for entry in self._read_entries(module_name, frid) if "target" in entry]
            if not entries:
                continue
            initial_issue = entries[0].get("issue_before") or _trim_issue(
                entries[0].get("issue_before_raw"), MAX_ISSUE_EXCERPT_CHARS
            )
            journal = {
                "module": module_name,
                "frid": frid,
                "initial_issue": initial_issue,
                "attempts": [self._digest_entry(entry) for entry in entries],
            }
            journals.append(journal)

        return journals

    def clear_all(self):
        """Deletes every journal. Called after the journals have been distilled into memories."""
        if os.path.exists(self.journal_folder):
            shutil.rmtree(self.journal_folder)
