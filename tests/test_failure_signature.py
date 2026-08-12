"""Tests for fingerprinting test script output.

``jest_network_failure.txt`` is a real captured conformance test run (a Jest suite against a live mock
server), not an invention. The shorter samples in this file are hand-written to exercise the masking rules
against the shapes other frameworks produce; they are not captures.
"""

import json
import os
import re

import pytest

from failure_signature import (
    BOILERPLATE_FREQUENCY_THRESHOLD,
    MAX_PROFILE_ENTRIES,
    MIN_RUNS_FOR_MATURE_PROFILE,
    PROFILE_FILE_NAME,
    LineFrequencyProfile,
    build_excerpt,
    compute_signature,
    mask_volatile,
    normalize_output,
)

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "data", "failure_output", "jest_network_failure.txt")


@pytest.fixture
def jest_output():
    with open(FIXTURE_PATH, "r", encoding="utf-8") as fixture:
        return fixture.read()


def _rerun_with_fresh_noise(output):
    """The same failure, from a run with different pids, ports, timings and shifted source lines."""
    output = output.replace("[87920]", "[91455]").replace("localhost:8003", "localhost:8117")
    output = re.sub(r"\((\d+) ms\)", lambda match: f"({int(match.group(1)) + 37} ms)", output)
    output = output.replace("(15.358 s)", "(14.902 s)")
    output = re.sub(
        r"test\.ts:(\d+):(\d+)", lambda match: f"test.ts:{int(match.group(1)) + 4}:{match.group(2)}", output
    )
    # Retry loops settle after a different number of attempts from one run to the next.
    return output.replace("Waiting for The Snap-In to start...\n" * 11, "Waiting for The Snap-In to start...\n" * 4)


def _mature_profile(*outputs):
    profile = LineFrequencyProfile()
    for output in outputs:
        profile.observe(output)
    return profile


# --- masking -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line, expected_marker",
    [
        ("Finished in 12.34s", "<DURATION>"),
        ("test took 1098 ms", "<DURATION>"),
        ("2026-08-12T14:03:22.881Z starting", "<TIMESTAMP>"),
        ("object at 0x7f8a1c2d3e4f", "<ADDR>"),
        ("com.example.Foo@1a2b3c4d5e", "<ADDR>"),
        ("id 550e8400-e29b-41d4-a716-446655440000", "<UUID>"),
        ("listening on localhost:8080", "<HOST>"),
        ("connect to 192.168.1.14", "<IP>"),
        ("wrote /tmp/pytest-of-user/run42/out.json", "<TMPDIR>"),
        ("INFO: Started server process [87920]", "[<PID>]"),
        ("  at handler.py:127:14", ":<LINE>:<COL>"),
        ("commit 4f9a2be1c7d", "<HASH>"),
    ],
)
def test_volatile_tokens_are_masked(line, expected_marker):
    assert expected_marker in mask_volatile(line)


def test_home_directories_are_collapsed_so_records_do_not_carry_a_username():
    assert mask_volatile("--setup=/Users/testuser/Dev/shared/jest.setup.js") == "--setup=~/Dev/shared/jest.setup.js"
    assert "alice" not in mask_volatile(r"loading C:\Users\alice\proj\conf.yaml")


def test_bare_numbers_survive_because_they_carry_the_assertion():
    """Masking every number would merge "got 3" with "got 4", erasing the distinction worth keeping."""
    assert mask_volatile("AssertionError: expected 5 but got 3") == "AssertionError: expected 5 but got 3"
    assert mask_volatile("expected 5 but got 3") != mask_volatile("expected 5 but got 4")


def test_source_gutters_lose_their_line_numbers():
    """The number in a quoted source excerpt shifts whenever the file above it is edited."""
    assert mask_volatile("  106 |       throw new Error(msg);") == "  <LINE> |       throw new Error(msg);"
    assert mask_volatile("> 108 |       throw new Error(msg);") == "> <LINE> |       throw new Error(msg);"


def test_ansi_colour_codes_are_stripped():
    assert mask_volatile("\x1b[31mFAILED\x1b[0m tests/test_a.py") == "FAILED tests/test_a.py"


# --- normalization -------------------------------------------------------------------------------------


def test_repeated_lines_collapse_to_first_occurrence_in_order():
    normalized = normalize_output("start\nretry\nretry\nretry\ndone\n")
    assert normalized == ["start", "retry", "done"]


def test_a_run_that_merely_retried_a_different_number_of_times_normalizes_identically():
    assert normalize_output("waiting\n" * 11 + "up\n") == normalize_output("waiting\n" * 4 + "up\n")


def test_blank_lines_are_dropped():
    assert normalize_output("a\n\n\n   \nb\n") == ["a", "b"]


def test_normalization_compresses_a_real_run_substantially(jest_output):
    """The real capture repeats one failure block once per affected test."""
    normalized = normalize_output(jest_output)
    assert len(normalized) < len(jest_output.splitlines()) / 3


def test_empty_output_normalizes_to_nothing():
    assert normalize_output("") == []
    assert normalize_output("   \n\n") == []


# --- the profile ---------------------------------------------------------------------------------------


def test_a_young_profile_declines_to_call_anything_boilerplate(jest_output):
    profile = _mature_profile(jest_output)
    assert not profile.is_mature
    assert not profile.is_boilerplate("INFO: Application startup complete.")


def test_profile_matures_after_enough_runs(jest_output):
    profile = _mature_profile(*([jest_output] * MIN_RUNS_FOR_MATURE_PROFILE))
    assert profile.is_mature


def test_lines_common_to_every_run_are_boilerplate_and_the_failure_is_not(jest_output):
    passing_run = jest_output.split("FAIL ./can-invoke-extraction.test.ts")[0] + "PASS (12.004 s)\nTests: 14 passed\n"
    profile = _mature_profile(jest_output, _rerun_with_fresh_noise(jest_output), passing_run, passing_run)

    assert profile.is_boilerplate("Starting mock DevRev server...")
    assert not profile.is_boilerplate("    All 3 attempts failed. Network error: ECONNREFUSED -")


def test_a_pid_that_changes_every_run_is_still_recognised_as_boilerplate(jest_output):
    """Targeted masking may miss a volatile number; the digit-blind skeleton catches it anyway."""
    runs = [jest_output.replace("[87920]", f"[9{index}455]") for index in range(4)]
    profile = _mature_profile(*runs)

    assert profile.is_boilerplate("INFO:     Started server process [<PID>]")


def test_the_profile_is_capped_and_evicts_the_rarest_lines():
    profile = LineFrequencyProfile()
    profile.observe("\n".join(f"unique line {index}" for index in range(MAX_PROFILE_ENTRIES + 500)))
    profile.observe("recurring line")
    profile.observe("recurring line")

    assert len(profile.line_counts) <= MAX_PROFILE_ENTRIES


def test_profile_survives_a_save_and_load_round_trip(tmp_path, jest_output):
    profile = _mature_profile(jest_output, jest_output, jest_output)
    profile.save(str(tmp_path))

    reloaded = LineFrequencyProfile.load(str(tmp_path))
    assert reloaded.run_count == profile.run_count
    assert reloaded.line_counts == profile.line_counts


def test_a_corrupt_profile_is_discarded_rather_than_raising(tmp_path):
    with open(os.path.join(str(tmp_path), PROFILE_FILE_NAME), "w", encoding="utf-8") as corrupt:
        corrupt.write("{not json at all")

    profile = LineFrequencyProfile.load(str(tmp_path))
    assert profile.run_count == 0


def test_a_missing_profile_loads_as_empty(tmp_path):
    assert LineFrequencyProfile.load(os.path.join(str(tmp_path), "nope")).run_count == 0


def test_profile_is_written_outside_the_folder_memory_files_are_read_from(tmp_path):
    """The profile must never be picked up and fed into a prompt as if it were a memory."""
    profile = _mature_profile("a\nb\n")
    profile.save(str(tmp_path))

    saved = json.load(open(os.path.join(str(tmp_path), PROFILE_FILE_NAME), encoding="utf-8"))
    assert set(saved) == {"run_count", "line_counts"}
    assert not os.path.exists(os.path.join(str(tmp_path), "conformance_test_memory", PROFILE_FILE_NAME))


# --- the signature -------------------------------------------------------------------------------------


def test_the_same_failure_with_fresh_noise_keeps_its_signature(jest_output):
    rerun = _rerun_with_fresh_noise(jest_output)
    profile = _mature_profile(jest_output, rerun, jest_output, rerun)

    assert compute_signature(jest_output, 1, profile) == compute_signature(rerun, 1, profile)


def test_a_genuinely_different_failure_gets_a_different_signature(jest_output):
    other_failure = jest_output.replace(
        "All 3 attempts failed. Network error: ECONNREFUSED -",
        'Server returned 422: {"error":"missing required field event_context"}',
    )
    profile = _mature_profile(jest_output, _rerun_with_fresh_noise(jest_output), other_failure, other_failure)

    assert compute_signature(jest_output, 1, profile) != compute_signature(other_failure, 1, profile)


def test_a_different_exit_code_is_a_different_failure(jest_output):
    """Identical text with a different exit code is a different outcome - a timeout kill, say, not a failure."""
    passing_run = jest_output.split("FAIL ./can-invoke-extraction.test.ts")[0] + "PASS (12.004 s)\n"
    profile = _mature_profile(jest_output, _rerun_with_fresh_noise(jest_output), passing_run, passing_run)

    failed = compute_signature(jest_output, 1, profile)
    killed = compute_signature(jest_output, 137, profile)
    assert failed is not None
    assert failed != killed


def test_line_order_does_not_affect_the_signature():
    """Parallel runners interleave output differently between runs."""
    profile = _mature_profile("common\nfailure A\nfailure B\n", "common\nother\n", "common\nanother\n")

    forwards = compute_signature("common\nfailure A\nfailure B\n", 1, profile)
    backwards = compute_signature("common\nfailure B\nfailure A\n", 1, profile)
    assert forwards is not None and forwards == backwards


def test_signature_is_unknown_while_the_profile_is_young(jest_output):
    assert compute_signature(jest_output, 1, _mature_profile(jest_output)) is None


def test_signature_is_unknown_when_every_line_is_boilerplate():
    """With nothing distinctive left there is no honest identity to report."""
    profile = _mature_profile("same\n", "same\n", "same\n", "same\n")

    assert compute_signature("same\n", 1, profile) is None


def test_signature_is_unknown_for_empty_output():
    profile = _mature_profile("a\n", "b\n", "c\n")

    assert compute_signature("", 1, profile) is None
    assert compute_signature("   \n", 1, profile) is None


# --- the excerpt ---------------------------------------------------------------------------------------


def test_the_excerpt_keeps_what_actually_failed(jest_output):
    excerpt = build_excerpt(jest_output)

    assert "ECONNREFUSED" in excerpt
    assert "should successfully invoke with valid event" in excerpt


def test_the_excerpt_keeps_boilerplate_that_gives_the_reader_context(jest_output):
    """Unlike the signature, the excerpt is for reading, so the surrounding run context stays."""
    assert "Running conformance tests" in build_excerpt(jest_output)


def test_the_excerpt_is_capped_and_says_so(jest_output):
    excerpt = build_excerpt(jest_output, max_lines=10)

    assert len(excerpt.splitlines()) == 11
    assert "further lines omitted" in excerpt


def test_the_excerpt_does_not_end_on_a_half_truncated_line(jest_output):
    excerpt = build_excerpt(jest_output, max_chars=300)
    body, marker = excerpt.rsplit("\n", 1)

    assert "further lines omitted" in marker
    assert body in "\n".join(normalize_output(jest_output))


def test_an_uncapped_excerpt_carries_no_truncation_marker():
    assert build_excerpt("just this\n") == "just this"


def test_the_excerpt_is_unknown_for_empty_output():
    assert build_excerpt("") is None
    assert build_excerpt("  \n\n") is None


def test_the_excerpt_of_a_real_run_is_far_smaller_than_the_run(jest_output):
    assert len(build_excerpt(jest_output)) < len(jest_output) / 2


# --- shapes from other frameworks ----------------------------------------------------------------------


PYTEST_OUTPUT = """============================= test session starts ==============================
platform darwin -- Python 3.11.8, pytest-8.3.4
rootdir: /Users/someone/proj
collected 14 items

tests/test_profile.py .....F........                                     [100%]

=================================== FAILURES ===================================
_________________________ test_gender_status_is_nodata _________________________

    def test_gender_status_is_nodata():
        result = client.get("/profile/42")
>       assert result.json()["gender"]["status"] == "exact"
E       AssertionError: assert 'nodata' == 'exact'

tests/test_profile.py:88: AssertionError
=========================== short test summary info ============================
FAILED tests/test_profile.py::test_gender_status_is_nodata - AssertionError
========================= 1 failed, 13 passed in 2.41s =========================
"""

MAVEN_OUTPUT = """[INFO] Scanning for projects...
[INFO] Building example 1.0.0-SNAPSHOT
[INFO] --- surefire:3.2.5:test (default-test) @ example ---
[ERROR] Tests run: 4, Failures: 1, Errors: 0, Skipped: 0
[ERROR] ProfileTest.genderStatus:88 expected:<exact> but was:<nodata>
[INFO] BUILD FAILURE
[INFO] Total time:  8.412 s
[INFO] Finished at: 2026-08-12T14:03:22+02:00
"""

GO_OUTPUT = """=== RUN   TestGenderStatus
    profile_test.go:88: expected exact, got nodata
--- FAIL: TestGenderStatus (0.00s)
FAIL
FAIL    example/profile 0.412s
"""


@pytest.mark.parametrize(
    "output, assertion_text",
    [
        (PYTEST_OUTPUT, "assert 'nodata' == 'exact'"),
        (MAVEN_OUTPUT, "expected:<exact> but was:<nodata>"),
        (GO_OUTPUT, "expected exact, got nodata"),
    ],
)
def test_the_assertion_survives_normalization_whatever_the_framework(output, assertion_text):
    assert any(assertion_text in line for line in normalize_output(output))


@pytest.mark.parametrize("output", [PYTEST_OUTPUT, MAVEN_OUTPUT, GO_OUTPUT])
def test_timings_are_masked_so_a_rerun_is_not_mistaken_for_a_change(output):
    slower = re.sub(r"(\d+)\.(\d+)( ?s\b)", r"\g<1>9.\g<2>\g<3>", output)

    assert normalize_output(output) == normalize_output(slower)


def test_boilerplate_detection_finds_the_maven_epilogue_and_leaves_the_failure():
    """Maven buries a handful of meaningful lines in build chatter that is identical every run."""
    passing = MAVEN_OUTPUT.replace(
        "[ERROR] Tests run: 4, Failures: 1, Errors: 0, Skipped: 0\n"
        "[ERROR] ProfileTest.genderStatus:88 expected:<exact> but was:<nodata>\n",
        "[INFO] Tests run: 4, Failures: 0, Errors: 0, Skipped: 0\n",
    ).replace("BUILD FAILURE", "BUILD SUCCESS")
    profile = _mature_profile(MAVEN_OUTPUT, MAVEN_OUTPUT, passing, passing)

    assert profile.is_boilerplate("[INFO] Scanning for projects...")
    assert not profile.is_boilerplate("[ERROR] ProfileTest.genderStatus:<LINE> expected:<exact> but was:<nodata>")


def test_boilerplate_threshold_needs_a_clear_majority_of_runs():
    """A line in half the runs is a real signal, not furniture."""
    profile = _mature_profile("common\nsometimes\n", "common\n", "common\nsometimes\n", "common\n")

    assert BOILERPLATE_FREQUENCY_THRESHOLD > 0.5
    assert profile.is_boilerplate("common")
    assert not profile.is_boilerplate("sometimes")
