"""Fingerprint stability tests.

The whole evidential memory system keys off these fingerprints, so the properties that
matter are: the same failure collapses to one fingerprint despite run-to-run noise, and
genuinely different failures do not collide.
"""

from memory_management.fingerprint import compute_fingerprint, extract_signature, fingerprint_output, normalize_output

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


def test_same_pytest_failure_is_stable_across_runs():
    assert _fingerprint(PYTEST_FAILURE_RUN_1) == _fingerprint(PYTEST_FAILURE_RUN_2)


def test_same_surefire_failure_is_stable_across_runs():
    assert _fingerprint(SUREFIRE_FAILURE_RUN_1) == _fingerprint(SUREFIRE_FAILURE_RUN_2)


def test_same_jest_failure_is_stable_across_runs():
    assert _fingerprint(JEST_FAILURE_RUN_1) == _fingerprint(JEST_FAILURE_RUN_2)


def test_different_failures_do_not_collide():
    assert _fingerprint(PYTEST_FAILURE_RUN_1) != _fingerprint(PYTEST_DIFFERENT_FAILURE)


def test_failures_from_different_runners_do_not_collide():
    fingerprints = {
        _fingerprint(PYTEST_FAILURE_RUN_1),
        _fingerprint(SUREFIRE_FAILURE_RUN_1),
        _fingerprint(JEST_FAILURE_RUN_1),
    }
    assert len(fingerprints) == 3


def test_passing_or_empty_output_has_no_fingerprint():
    for raw in (None, "", "   \n  \n"):
        fingerprint, signature, excerpt = fingerprint_output(raw)
        assert fingerprint is None
        assert signature == ""
        assert excerpt == ""


def test_normalization_removes_run_varying_tokens():
    raw = (
        "2026-08-20T10:14:22Z ERROR at /private/tmp/build_ab12/code/src/tasks.py:47 "
        "object 0x7f9c1a2b3c4d uuid 3f2504e0-4f89-11d3-9a0c-0305e82c3301 took 1.25s"
    )
    normalized = normalize_output(raw)

    assert "<TS>" in normalized
    assert "<ADDR>" in normalized
    assert "<UUID>" in normalized
    assert "<DUR>" in normalized
    # The path collapses to its basename: the file name is signal, the prefix is not.
    assert "tasks.py" in normalized
    assert "/private/tmp" not in normalized
    assert "build_ab12" not in normalized


def test_signature_prefers_failure_lines_over_runner_chatter():
    signature = extract_signature(normalize_output(PYTEST_FAILURE_RUN_1))

    assert signature
    assert "session starts" not in signature
    assert any("assert" in line.lower() for line in signature.splitlines())


def test_signature_falls_back_to_tail_when_no_marker_matches():
    unrecognised = "step one done\nstep two done\nsomething odd happened at the end"
    signature = extract_signature(normalize_output(unrecognised))

    assert "something odd happened at the end" in signature


def test_fingerprint_is_deterministic_and_short():
    signature = "AssertionError: expected <N> got <N>"
    first = compute_fingerprint(signature)

    assert first == compute_fingerprint(signature)
    assert first is not None
    assert len(first) == 12


def test_excerpt_is_bounded():
    raw = "AssertionError: boom\n" + ("noise line here\n" * 5000)
    _, _, excerpt = fingerprint_output(raw)

    assert len(excerpt) <= 1500 + len("...\n")
