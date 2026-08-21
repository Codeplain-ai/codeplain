"""Persistence for evidential memory records.

The store is render-scoped: one folder per render, shared by every module in the
``requires`` chain, because a failure observed while rendering one module is just as
relevant to the next one.

Writing a record involves no LLM call and costs no credit. Everything persisted here is
either observed directly (exit codes, diffs, test output) or derived deterministically
from those observations.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from typing import Optional

from memory_management.record import Failure, Intervention, MemoryRecord, Scope, Status, build_record
from memory_management.rendering import render
from memory_management.retrieval import MemoryMode, select_memory
from plain2code_console import console

# The payload carries one rendered block rather than one entry per record. The name is part
# of the client/server contract: the server recognises it and passes the block through
# under its own explanatory preamble, instead of grouping records itself.
MEMORY_BLOCK_FILE_NAME = "memory.md"


class MemoryStore:
    """Append-only store of objective observations for the duration of one render.

    Nothing is ever deleted. A refuted observation ("this intervention did not fix this
    failure") is as objectively true as a verified one, and inside a long fix loop it is
    the more useful of the two because it prunes the search space.
    """

    def __init__(self, memory_folder: str, memory_mode: MemoryMode = MemoryMode.ALL):
        self.memory_folder = memory_folder
        self.memory_mode = memory_mode

    # --- lifecycle ----------------------------------------------------------------

    def clear(self) -> None:
        """Drop every record. Called once at the start of a full render."""
        if os.path.exists(self.memory_folder):
            shutil.rmtree(self.memory_folder)
            console.debug(f"Cleared memory store at {self.memory_folder}.")

    # --- reading ------------------------------------------------------------------

    def load_all(self) -> list[MemoryRecord]:
        """Load every record, skipping any file that is not a readable record."""
        if not os.path.exists(self.memory_folder):
            return []

        records: list[MemoryRecord] = []
        for file_name in sorted(os.listdir(self.memory_folder)):
            if not file_name.endswith(".json"):
                continue
            file_path = os.path.join(self.memory_folder, file_name)
            try:
                with open(file_path, "r") as memory_file:
                    records.append(MemoryRecord.from_json(memory_file.read()))
            except (OSError, ValueError, KeyError) as exception:
                console.debug(f"Skipping unreadable memory file {file_name}: {exception}")

        return records

    def retrieve(
        self,
        testing_module: Optional[str] = None,
        testing_frid: Optional[str] = None,
        fingerprint: Optional[str] = None,
        test_name: Optional[str] = None,
        files_changed: Optional[list[str]] = None,
        failure_text: Optional[str] = None,
        fix_attempts: int = 0,
        suite: Optional[str] = None,
    ) -> dict[str, str]:
        """Select the memory relevant to the fix currently being attempted.

        Two things are returned together: every attempt already made against this
        functionality, and ranked evidence from elsewhere in the render. The first is what
        narrows the search space, so it is always included in full; the second is budgeted.

        Returns ``{file_name: text}`` - the shape the API payload has always used, so the
        wire contract is unchanged. The content is one rendered block: memory is not
        cacheable in the prompt, so a record costs its full token price on every call, and
        a table with one legend beats a JSON document per record by an order of magnitude.
        """
        if self.memory_mode is MemoryMode.OFF:
            return {}

        result = select_memory(
            self.load_all(),
            testing_module=testing_module,
            testing_frid=testing_frid,
            suite=suite,
            fingerprint=fingerprint,
            test_name=test_name,
            files_changed=files_changed,
            failure_text=failure_text,
            fix_attempts=fix_attempts,
            mode=self.memory_mode,
        )

        block = render(result)
        if not block:
            return {}

        console.debug(
            f"Retrieved {len(result.loop_history)} previous attempt(s) against this functionality "
            f"and {len(result.associative)} related observation(s) from elsewhere in the render."
        )

        return {MEMORY_BLOCK_FILE_NAME: block}

    # --- writing ------------------------------------------------------------------

    def record_observation(
        self,
        scope: Scope,
        failure: Failure,
        intervention: Intervention,
        exit_code_after: int,
        fingerprint_after: Optional[str],
        render_id: Optional[str] = None,
    ) -> MemoryRecord:
        """Persist one failure -> intervention -> outcome observation."""
        record = build_record(
            scope=scope,
            failure=failure,
            intervention=intervention,
            exit_code_after=exit_code_after,
            fingerprint_after=fingerprint_after,
            observed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            render_id=render_id,
        )

        existing = self._load_one(record.file_name)
        if existing is not None:
            record = _merge(existing, record)

        self._write(record)
        console.debug(
            f"Recorded {record.status} memory {record.memory_id} "
            f"(transition {record.outcome.transition}, occurrence {record.occurrences})."
        )
        return record

    # --- internals ----------------------------------------------------------------

    def _load_one(self, file_name: str) -> Optional[MemoryRecord]:
        file_path = os.path.join(self.memory_folder, file_name)
        if not os.path.exists(file_path):
            return None
        try:
            with open(file_path, "r") as memory_file:
                return MemoryRecord.from_json(memory_file.read())
        except (OSError, ValueError, KeyError):
            return None

    def _write(self, record: MemoryRecord) -> None:
        os.makedirs(self.memory_folder, exist_ok=True)
        with open(os.path.join(self.memory_folder, record.file_name), "w") as memory_file:
            memory_file.write(record.to_json())


def _merge(existing: MemoryRecord, observed: MemoryRecord) -> MemoryRecord:
    """Fold a repeat observation of the same attempt into the stored record.

    The same intervention against the same failure can be observed more than once in a
    render. The repeat is counted rather than stored twice. If the repeat resolved the
    failure while the stored record did not, the stored record is promoted - a later
    green run is a strictly better-grounded observation than an earlier red one.
    """
    promoted = existing.status != Status.VERIFIED.value and observed.status == Status.VERIFIED.value
    winner = observed if promoted else existing

    return MemoryRecord(
        memory_id=winner.memory_id,
        scope=winner.scope,
        failure=winner.failure,
        intervention=winner.intervention,
        outcome=winner.outcome,
        status=winner.status,
        attribution_confidence=winner.attribution_confidence,
        observed_at=winner.observed_at,
        render_id=winner.render_id,
        flags=winner.flags,
        occurrences=existing.occurrences + 1,
        schema_version=winner.schema_version,
    )
