"""Tests for cause extraction and failure identity.

The whole memory system keys off these fingerprints, so three properties matter:

* the same failure collapses to one fingerprint despite run-to-run noise
* genuinely different failures do not collide - including failures that differ only in a
  value, like an HTTP status code
* the extracted cause is the line that says what went wrong, not the wrapper around it or
  the runner's exit commentary

The fixtures under ``tests/data/test_output`` are reconstructions of real output from a
Java/Maven render. The two Spring fixtures reproduce the exact fingerprints that render
produced, which is what makes them a regression test rather than an illustration.
"""

import os

import pytest

from memory_management.fingerprint import (
    CAUSE_MAX_CHARS,
    FINGERPRINT_LENGTH,
    compute_fingerprint,
    extract_causes,
    fingerprint_output,
    normalize_cause,
)

FIXTURE_FOLDER = os.path.join(os.path.dirname(__file__), "data", "test_output")


def read_fixture(name):
    with open(os.path.join(FIXTURE_FOLDER, f"{name}.txt")) as fixture:
        return fixture.read()


PYTEST_FAILURE_RUN_1 = """
============================= test session starts ==============================
platform darwin -- Python 3.11.14, pytest-8.3.4, pluggy-1.5.0
rootdir: /private/tmp/build_a1b2c3/code
collected 12 items

tests/test_tasks.py ..F.........                                         [100%]

=================================== FAILURES ===================================
____________________ test_add_task_rejects_empty_content _______________________

    def test_add_task_rejects_empty_content():
        response = client.post("/tasks", json={"content": ""})
>       assert response.status_code == 400
E       assert 500 == 400

tests/test_tasks.py:47: AssertionError
=========================== short test summary info ============================
FAILED tests/test_tasks.py::test_add_task_rejects_empty_content - assert 500 == 400
========================= 1 failed, 11 passed in 0.42s =========================
"""

# Same failure, different run: new build folder, shifted line numbers, different timing,
# different collection count.
PYTEST_FAILURE_RUN_2 = """
============================= test session starts ==============================
platform darwin -- Python 3.11.14, pytest-8.3.4, pluggy-1.5.0
rootdir: /private/tmp/build_9f8e7d/code
collected 14 items

tests/test_tasks.py ..F...........                                       [100%]

=================================== FAILURES ===================================
____________________ test_add_task_rejects_empty_content _______________________

    def test_add_task_rejects_empty_content():
        response = client.post("/tasks", json={"content": ""})
>       assert response.status_code == 400
E       assert 500 == 400

tests/test_tasks.py:63: AssertionError
=========================== short test summary info ============================
FAILED tests/test_tasks.py::test_add_task_rejects_empty_content - assert 500 == 400
========================= 1 failed, 13 passed in 1.07s =========================
"""

# A genuinely different failure in the same suite.
PYTEST_DIFFERENT_FAILURE = """
=================================== FAILURES ===================================
_________________________ test_list_tasks_pagination ___________________________

    def test_list_tasks_pagination():
>       assert len(response.json()["items"]) == 10
E       KeyError: 'items'

tests/test_tasks.py:88: KeyError
=========================== short test summary info ============================
FAILED tests/test_tasks.py::test_list_tasks_pagination - KeyError: 'items'
========================= 1 failed, 13 passed in 0.55s =========================
"""

SUREFIRE_FAILURE_RUN_1 = """
[INFO] -------------------------------------------------------
[INFO]  T E S T S
[INFO] -------------------------------------------------------
[INFO] Running com.example.integrations.foo.ClientTest
[ERROR] Tests run: 4, Failures: 1, Errors: 0, Skipped: 0, Time elapsed: 2.145 s
[ERROR] shouldRejectEmptyPayload  Time elapsed: 0.031 s  <<< FAILURE!
org.junit.ComparisonFailure: expected:<400> but was:<500>
    at com.example.integrations.foo.ClientTest.shouldRejectEmptyPayload(ClientTest.java:52)
[INFO] BUILD FAILURE
[INFO] Total time:  8.412 s
"""

SUREFIRE_FAILURE_RUN_2 = """
[INFO] -------------------------------------------------------
[INFO]  T E S T S
[INFO] -------------------------------------------------------
[INFO] Running com.example.integrations.foo.ClientTest
[ERROR] Tests run: 6, Failures: 1, Errors: 0, Skipped: 0, Time elapsed: 3.902 s
[ERROR] shouldRejectEmptyPayload  Time elapsed: 0.048 s  <<< FAILURE!
org.junit.ComparisonFailure: expected:<400> but was:<500>
    at com.example.integrations.foo.ClientTest.shouldRejectEmptyPayload(ClientTest.java:71)
[INFO] BUILD FAILURE
[INFO] Total time:  11.008 s
"""

JEST_FAILURE_RUN_1 = """
FAIL  src/tasks.test.ts
  ● adds a task › rejects empty content

    expect(received).toBe(expected) // Object.is equality

    Expected: 400
    Received: 500

      at Object.<anonymous> (/Users/dev/proj/src/tasks.test.ts:31:24)

Tests:       1 failed, 9 passed, 10 total
Time:        2.51 s
"""

JEST_FAILURE_RUN_2 = """
FAIL  src/tasks.test.ts
  ● adds a task › rejects empty content

    expect(received).toBe(expected) // Object.is equality

    Expected: 400
    Received: 500

      at Object.<anonymous> (/tmp/ci_run_88f1/src/tasks.test.ts:44:18)

Tests:       1 failed, 12 passed, 13 total
Time:        4.02 s
"""


def _fingerprint(raw):
    return fingerprint_output(raw)[0]


def _causes(raw):
    return fingerprint_output(raw)[1]


# --- stability across runs --------------------------------------------------------


@pytest.mark.parametrize(
    "first,second",
    [
        (PYTEST_FAILURE_RUN_1, PYTEST_FAILURE_RUN_2),
        (SUREFIRE_FAILURE_RUN_1, SUREFIRE_FAILURE_RUN_2),
        (JEST_FAILURE_RUN_1, JEST_FAILURE_RUN_2),
    ],
)
def test_the_same_failure_is_stable_across_runs(first, second):
    """Different build folders, line numbers, timings and test counts, one identity."""
    assert _fingerprint(first) == _fingerprint(second)


def test_different_failures_do_not_collide():
    assert _fingerprint(PYTEST_FAILURE_RUN_1) != _fingerprint(PYTEST_DIFFERENT_FAILURE)


def test_failures_from_different_runners_do_not_collide():
    fingerprints = {
        _fingerprint(PYTEST_FAILURE_RUN_1),
        _fingerprint(SUREFIRE_FAILURE_RUN_1),
        _fingerprint(JEST_FAILURE_RUN_1),
    }

    assert len(fingerprints) == 3


def test_passing_or_empty_output_has_no_identity():
    for output in [None, "", "   \n\n", "All tests passed."]:
        fingerprint, causes = fingerprint_output(output)
        if output in (None, "", "   \n\n"):
            assert (fingerprint, causes) == (None, [])


def test_fingerprint_is_deterministic_and_short():
    first = _fingerprint(PYTEST_FAILURE_RUN_1)

    assert first == _fingerprint(PYTEST_FAILURE_RUN_1)
    assert len(first) == FINGERPRINT_LENGTH


# --- the regression case from a real render ---------------------------------------


def test_one_root_cause_gives_one_identity_across_test_classes():
    """The case that broke: two modules, same slf4j/Logback conflict, two fingerprints.

    The Spring wrapper embeds `testClass = ...` in a 1500-character configuration dump, so
    including it made an identical failure look different in every test class.
    """
    http_client = read_fixture("maven_spring_logback_http_client")
    version_api = read_fixture("maven_spring_logback_version_api")

    assert _fingerprint(http_client) == _fingerprint(version_api)


def test_the_innermost_cause_is_what_gets_extracted():
    causes = _causes(read_fixture("maven_spring_logback_http_client"))

    assert causes == ["LoggerFactory is not a Logback LoggerContext but Logback is on the classpath"]


def test_a_value_difference_is_a_different_failure():
    """`expected 200 but was 401` and `... but was 500` are not the same bug."""
    unauthorized = read_fixture("junit_status_401")
    server_error = read_fixture("junit_status_500")

    assert _fingerprint(unauthorized) != _fingerprint(server_error)
    assert _causes(unauthorized) == ["expected: <200> but was: <401>"]
    assert _causes(server_error) == ["expected: <200> but was: <500>"]


def test_a_compile_error_is_found_rather_than_the_build_banner():
    """`[ERROR]` is not `Error`; a case-sensitive marker used to miss this entirely."""
    causes = _causes(read_fixture("javac_compile_error"))

    assert causes == ["D365HttpClient.java:[87,23] cannot find symbol"]


def test_a_go_failure_keeps_the_values_that_distinguish_it():
    assert _causes(read_fixture("go_test_failure")) == ["parseConfig() timeout = 0s, want 30s"]


def test_a_pytest_failure_uses_the_short_summary_line():
    assert _causes(read_fixture("pytest_assertion")) == ["AssertionError: assert <Task id=4> is None"]


def test_a_jest_failure_keeps_the_expectation_and_both_sides():
    causes = _causes(read_fixture("jest_assertion"))

    assert "expect(received).toHaveLength(expected)" in causes
    assert "Expected length: 3" in causes
    assert "Received length: 2" in causes


# --- what is excluded from candidacy ----------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "\tat org.springframework.util.Assert.isInstanceOf(Assert.java:606)",
        "[ERROR] Tests run: 6, Failures: 0, Errors: 6, Skipped: 0",
        "[INFO] BUILD FAILURE",
        "[INFO] Total time:  12.402 s",
        "[ERROR] Please refer to surefire-reports for the individual test results.",
        "[ERROR] -> [Help 1]",
        "========================= 1 failed, 11 passed in 0.84s =========================",
    ],
)
def test_runner_chatter_is_never_a_cause(line):
    """Each of these outranked the real cause in the previous implementation."""
    output = f"{line}\nCaused by: java.lang.IllegalStateException: the actual problem"

    assert extract_causes(output) == ["the actual problem"]


def test_output_with_no_recognizable_failure_still_yields_something():
    """Degrades to true-but-unhelpful, never to empty and never to invented."""
    causes = extract_causes("some unstructured tool wrote this\nand then gave up")

    assert causes
    assert all(cause for cause in causes)


# --- cause normalization ----------------------------------------------------------


def test_volatile_tokens_are_removed_from_a_cause():
    cause = normalize_cause(
        "Caused by: java.io.IOException: cannot read /Users/dev/proj/build_a1/data.csv "
        "at 2026-08-21T07:34:31 (handle@4d9e68d0)"
    )

    assert "/Users/dev" not in cause
    assert "data.csv" in cause
    assert "<TS>" in cause
    assert "@<HASH>" in cause


def test_a_long_cause_is_cut_at_a_sentence_boundary():
    cause = normalize_cause(
        "java.lang.IllegalArgumentException: The real problem is stated first. "
        + "Then follows a great deal of advice that nobody needs. " * 6
    )

    assert cause == "The real problem is stated first"


def test_a_long_cause_without_sentences_is_truncated_and_marked():
    cause = normalize_cause("x" * (CAUSE_MAX_CHARS * 2))

    assert cause.endswith("...")
    assert len(cause) <= CAUSE_MAX_CHARS + 3


def test_no_causes_means_no_fingerprint():
    assert compute_fingerprint([]) is None
