from render_machine.failure_attribution import (
    attribute_failures,
    detect_layout_failure,
    extract_frid_failure_evidence,
    format_other_frids_note,
)

DELIMITER = "=" * 70
RULE = "-" * 70

CONFORMANCE_TESTS_JSON = {
    "1": {"folder_name": "conformance_tests/greeter/hello_world_display_conformance_tests"},
    "2": {"folder_name": "conformance_tests/greeter/shout_hello_world_conformance_tests"},
    "3": {"folder_name": "conformance_tests/greeter/quiet_mode_conformance_tests"},
}

TWO_SUITE_FAILURE_OUTPUT = f"""FF
{DELIMITER}
FAIL: test_greeting_output (hello_world_display_conformance_tests.test_conformance.TestGreeter.test_greeting_output)
{RULE}
Traceback (most recent call last):
  File "conformance_tests/greeter/hello_world_display_conformance_tests/test_conformance.py", line 19
AssertionError: greeting missing

{DELIMITER}
FAIL: test_shout_output (shout_hello_world_conformance_tests.test_conformance.TestShout.test_shout_output)
{RULE}
Traceback (most recent call last):
  File "conformance_tests/greeter/shout_hello_world_conformance_tests/test_conformance.py", line 19
AssertionError: shout missing

{RULE}
Ran 5 tests in 0.001s

FAILED (failures=2)
"""


def test_attribute_failures_multiple_implicated_in_spec_order():
    assert attribute_failures(TWO_SUITE_FAILURE_OUTPUT, CONFORMANCE_TESTS_JSON) == ["1", "2"]


def test_attribute_failures_single_implicated():
    output = "FAIL: test_x (quiet_mode_conformance_tests.test_conformance.TestQuiet.test_x)"
    assert attribute_failures(output, CONFORMANCE_TESTS_JSON) == ["3"]


def test_attribute_failures_none_implicated():
    assert attribute_failures("something unrelated failed", CONFORMANCE_TESTS_JSON) == []


def test_attribute_failures_handles_missing_folder_name():
    assert attribute_failures("anything", {"1": {}}) == []


def test_extract_evidence_keeps_only_matching_blocks_and_summary():
    evidence = extract_frid_failure_evidence(TWO_SUITE_FAILURE_OUTPUT, "hello_world_display_conformance_tests")

    assert "test_greeting_output" in evidence
    assert "greeting missing" in evidence
    assert "test_shout_output" not in evidence
    assert "shout missing" not in evidence
    assert "Ran 5 tests" in evidence
    assert "FAILED (failures=2)" in evidence


def test_extract_evidence_returns_full_output_without_delimiters():
    output = "some completely different runner format: suite failed"
    assert extract_frid_failure_evidence(output, "any_suite") == output


def test_extract_evidence_returns_full_output_when_no_block_matches():
    evidence = extract_frid_failure_evidence(TWO_SUITE_FAILURE_OUTPUT, "quiet_mode_conformance_tests")
    assert evidence == TWO_SUITE_FAILURE_OUTPUT


def test_extract_evidence_single_block_output():
    output = f"""F
{DELIMITER}
FAIL: test_only (quiet_mode_conformance_tests.test_conformance.TestQuiet.test_only)
{RULE}
Traceback (most recent call last):
AssertionError: quiet broken

{RULE}
Ran 1 test in 0.001s

FAILED (failures=1)
"""
    evidence = extract_frid_failure_evidence(output, "quiet_mode_conformance_tests")

    assert "quiet broken" in evidence
    assert "Ran 1 test" in evidence


def test_format_other_frids_note_lists_only_other_frids():
    note = format_other_frids_note(["1", "2"], current_frid="1")

    assert "2" in note
    assert "handled separately" in note
    assert "implementation code" in note


def test_format_other_frids_note_empty_when_only_current():
    assert format_other_frids_note(["1"], current_frid="1") == ""
    assert format_other_frids_note([], current_frid="1") == ""


def test_detect_layout_failure_on_unimportable_start_directory():
    output = "ImportError: Start directory is not importable: 'conformance_tests/greeter'"
    assert detect_layout_failure(output) is True


def test_detect_layout_failure_on_module_name_collision():
    output = (
        "ImportError: 'test_conformance' module incorrectly imported from 'suite_a'. "
        "Expected 'suite_b'. Is this module globally installed?"
    )
    assert detect_layout_failure(output) is True


def test_detect_layout_failure_on_no_suites_discovered():
    assert detect_layout_failure("Error: No conformance test suites discovered.") is True
    assert detect_layout_failure("Error: No unittests discovered.") is True


def test_detect_layout_failure_not_triggered_by_test_failures():
    assert detect_layout_failure(TWO_SUITE_FAILURE_OUTPUT) is False


def test_detect_layout_failure_not_triggered_when_tests_ran_despite_signature():
    output = "Ran 3 tests in 0.1s\nAssertionError: Start directory is not importable was printed by the app"
    assert detect_layout_failure(output) is False
