"""Helpers for attributing whole-suite conformance test failures to functionalities.

When the conformance test script runs a module's whole test suite, a failure can involve
tests belonging to several functionalities (FRIDs). These helpers map the failure output
back to the implicated FRIDs, extract the failure evidence belonging to one FRID, and
detect layout-level failures (e.g. suites from projects generated before whole-suite
execution that cannot be discovered together).
"""

import os
import re

# Failure block delimiter used by Python's unittest output.
_UNITTEST_BLOCK_DELIMITER = "=" * 70

# A horizontal rule inside/after unittest failure blocks and before the run summary.
_UNITTEST_SUMMARY_RULE = "-" * 70

_TESTS_RAN_PATTERN = re.compile(r"Ran [1-9]\d* tests?")

# Signatures of failures caused by the suite layout rather than by failing tests. Kept
# deliberately narrow: a legitimate test failure must never match. They are only
# consulted when the output shows that no test ran at all.
_LAYOUT_FAILURE_SIGNATURES = (
    # unittest discovery cannot import the start directory (missing package structure)
    "Start directory is not importable",
    # unittest discovery found two suites clashing on the same module name
    "module incorrectly imported from",
    # the python conformance script discovered no tests
    "No unittests discovered",
    # the golang/cypress conformance scripts found no suites to run
    "No conformance test suites discovered",
)


def attribute_failures(output: str, conformance_tests_json: dict) -> list[str]:
    """Return the FRIDs whose conformance test suite appears in the failure output.

    Matches each suite's folder basename against the output text. This is
    language-agnostic: test identifiers and paths in runner output contain the suite
    folder name (as a Python package, a path segment, or a suite header printed by the
    conformance script). The result follows the order of conformance_tests_json entries,
    which is spec order.
    """
    implicated_frids = []
    for frid, entry in conformance_tests_json.items():
        folder_basename = os.path.basename(entry.get("folder_name", ""))
        if folder_basename and folder_basename in output:
            implicated_frids.append(frid)

    return implicated_frids


def extract_frid_failure_evidence(output: str, folder_basename: str) -> str:
    """Extract the failure blocks belonging to one suite from the run output.

    Best-effort: understands Python unittest's "="-delimited failure blocks and keeps the
    run summary at the end. Returns the full output unchanged when the format doesn't
    cooperate (no delimiters, or no block mentions the suite) so no evidence is ever lost.
    """
    parts = output.split(_UNITTEST_BLOCK_DELIMITER)
    if len(parts) < 2:
        return output

    blocks = parts[1:]

    # The run summary ("Ran N tests..." / "FAILED (failures=N)") trails the last block
    # after a second horizontal rule. Split it off so it can be kept unconditionally.
    run_summary = ""
    last_block = blocks[-1]
    first_rule_position = last_block.find(_UNITTEST_SUMMARY_RULE)
    last_rule_position = last_block.rfind(_UNITTEST_SUMMARY_RULE)
    if last_rule_position != -1 and last_rule_position != first_rule_position:
        blocks[-1] = last_block[:last_rule_position]
        run_summary = last_block[last_rule_position:]

    matching_blocks = [block for block in blocks if folder_basename in block]
    if not matching_blocks:
        return output

    return "".join(_UNITTEST_BLOCK_DELIMITER + block for block in matching_blocks) + run_summary


def format_other_frids_note(implicated_frids: list[str], current_frid: str) -> str:
    """Summarize other implicated FRIDs for the fix prompt without exposing their traces.

    The fix call for one FRID must not carry raw failure details of tests whose files are
    not in its context. This note preserves the diagnostic signal (several functionalities
    failing at once points at the implementation code) while keeping the fix scoped.
    """
    other_frids = [frid for frid in implicated_frids if frid != current_frid]
    if not other_frids:
        return ""

    return (
        "\nNote: conformance tests of the following other functionalities also failed in this run: "
        + ", ".join(other_frids)
        + ". They are being handled separately - do not attempt to fix them or reference their files."
        + " Several functionalities failing at once usually indicates the root cause is in the"
        + " implementation code rather than in the conformance tests."
    )


def detect_layout_failure(output: str) -> bool:
    """Detect a failure caused by the suite layout rather than by failing tests.

    Conservative by design: returns True only when no test ran at all AND the output
    carries a known layout-failure signature. A legitimate test failure (which always
    reports at least one test run) never matches.
    """
    if _TESTS_RAN_PATTERN.search(output):
        return False

    return any(signature in output for signature in _LAYOUT_FAILURE_SIGNATURES)
