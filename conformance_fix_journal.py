"""Deterministic journal of conformance tests fix attempts.

While a functionality's conformance tests are failing, every fix attempt is recorded here: the
issue that triggered it, what the fixer said it tried, which files it changed and what the tests
reported afterwards. A digest of the journal is sent back with every fix request so the fixer does
not repeat attempts that already failed, and once the functionality's conformance tests all pass
the journal is distilled into durable memories and deleted.

Journals are keyed by the (module, frid) whose tests are being fixed - during the regression sweep
that can be a previously implemented functionality, possibly from a required module.
"""

import hashlib
import json
import os
import shutil

from plain2code_console import console

CONFORMANCE_TEST_JOURNAL_SUBFOLDER = "conformance_test_journal"

# The raw conformance tests output can be huge; only the tail is kept in a journal entry.
MAX_ISSUE_CHARS = 8000

# The keys of a journal entry that make up the digest sent to the fixer and the distiller.
DIGEST_KEYS = ["attempt", "hypothesis", "approach", "target", "files_changed", "duplicate_of", "result"]


def _trim_issue(issue: str | None) -> str | None:
    if issue is None or len(issue) <= MAX_ISSUE_CHARS:
        return issue
    return f"(TRUNCATED - showing the last {MAX_ISSUE_CHARS} characters)\n" + issue[-MAX_ISSUE_CHARS:]


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
        with open(journal_path, "r") as journal_file:
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
        with open(self._journal_path(module_name, frid), "w") as journal_file:
            for entry in entries:
                journal_file.write(json.dumps(entry) + "\n")

    def open_attempt(self, module_name: str, frid: str, attempt_no: int, issue_before: str):
        """Starts a new journal entry for a fix attempt that is about to be made."""
        entries = self._read_entries(module_name, frid)
        entries.append({"attempt": attempt_no, "issue_before": _trim_issue(issue_before)})
        self._write_entries(module_name, frid, entries)

    def record_fix(
        self,
        module_name: str,
        frid: str,
        fix_attempt_summary: dict | None,
        files_changed: list[str],
        target: str,
        diff_files: dict[str, str] | None,
    ):
        """Completes the open attempt with what the fix actually did.

        The diff hash marks the attempt as a duplicate when an earlier attempt in the same journal
        produced essentially the same change - the strongest signal that the fixer is going in
        circles.
        """
        entries = self._read_entries(module_name, frid)
        if not entries:
            console.error("Cannot record a fix - no journal entry is open.")
            return

        entry = entries[-1]
        if isinstance(fix_attempt_summary, dict):
            entry["hypothesis"] = fix_attempt_summary.get("hypothesis")
            entry["approach"] = fix_attempt_summary.get("approach")
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

    def record_result(self, module_name: str, frid: str, issue_after: str | None, passed: bool):
        """Records what the conformance tests reported after the last fix attempt was applied.

        Does nothing when no attempt is pending - the first test run of a functionality has no fix
        attempt behind it.
        """
        entries = self._read_entries(module_name, frid)
        if not entries or "result" in entries[-1]:
            return

        entry = entries[-1]
        entry["result"] = "conformance tests passed" if passed else "conformance tests still failed"
        if not passed:
            entry["issue_after"] = _trim_issue(issue_after)

        self._write_entries(module_name, frid, entries)

    def build_digest(self, module_name: str, frid: str) -> list[dict]:
        """Builds the compact attempt history sent with a fix request.

        Raw test outputs are left out - the current issue travels separately with the request, and
        the narrative of each attempt is carried by its summary, files and result.
        """
        return [self._digest_entry(entry) for entry in self._read_entries(module_name, frid)]

    @staticmethod
    def _digest_entry(entry: dict) -> dict:
        return {key: entry[key] for key in DIGEST_KEYS if key in entry}

    def collect_all(self) -> list[dict]:
        """Collects the digests of every journal, keyed by the tested module and frid, for distillation."""
        if not os.path.exists(self.journal_folder):
            return []

        journals = []
        for file_name in sorted(os.listdir(self.journal_folder)):
            if not file_name.endswith(".jsonl") or "__" not in file_name:
                continue
            module_name, _, frid = file_name[: -len(".jsonl")].rpartition("__")
            entries = self._read_entries(module_name, frid)
            if not entries:
                continue
            journal = {
                "module": module_name,
                "frid": frid,
                "initial_issue": entries[0].get("issue_before"),
                "attempts": [self._digest_entry(entry) for entry in entries],
            }
            journals.append(journal)

        return journals

    def clear_all(self):
        """Deletes every journal. Called after the journals have been distilled into memories."""
        if os.path.exists(self.journal_folder):
            shutil.rmtree(self.journal_folder)
