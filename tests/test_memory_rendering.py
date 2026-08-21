"""Tests for how retrieved memory is rendered into the prompt block.

The rendering exists for one reason: memory sits after the prompt-cache breakpoint, so it
is paid for in full on every call, and on the conformance path the same block goes into
five sub-prompts per attempt. What is asserted here is therefore both correctness and
economy - the block has to carry the facts and it has to stay small.
"""

from memory_management.record import Failure, Intervention, InterventionTarget, Scope, Status, Suite, build_record
from memory_management.rendering import LOOP_DIFF_ATTEMPTS, render
from memory_management.retrieval import RetrievalResult, build_loop_summary

STATE_A = "aaaaaaaaaaaa"
STATE_B = "bbbbbbbbbbbb"
CAUSE_A = "LoggerFactory is not a Logback LoggerContext but Logback is on the classpath"
CAUSE_B = "expected: <200> but was: <401>"


def attempt(
    index,
    before=STATE_A,
    after=STATE_B,
    causes=(CAUSE_A,),
    files=("pom.xml",),
    lines=36,
    target=InterventionTarget.CONFORMANCE_TESTS.value,
    module="d365_http_client",
    frid="1",
    resolved=False,
    diff=None,
):
    return build_record(
        scope=Scope(
            module=module,
            frid=frid,
            testing_module=module,
            testing_frid=frid,
            suite=Suite.CONFORMANCE.value,
            test_name="http_client_conformance_tests",
        ),
        failure=Failure(fingerprint=before, causes=list(causes), exit_code=1),
        intervention=Intervention(
            attempt_index=index,
            target=target,
            files_changed=list(files),
            lines_changed=lines,
            touched_test_files=target == InterventionTarget.CONFORMANCE_TESTS.value,
            diff=diff,
        ),
        exit_code_after=0 if resolved else 1,
        fingerprint_after=None if resolved else after,
        observed_at=f"2026-08-21T07:{index:02d}:00Z",
    )


def result(loop_history=(), associative=()):
    history = list(loop_history)
    return RetrievalResult(
        loop_history=history,
        associative=list(associative),
        loop_summary=build_loop_summary(
            history,
            attempts_listed=len(history),
            testing_module="d365_http_client",
            testing_frid="1",
            suite=Suite.CONFORMANCE.value,
        ),
    )


# --- the loop table ---------------------------------------------------------------


def test_each_attempt_is_one_row():
    block = render(result([attempt(1), attempt(2, before=STATE_B, after=STATE_A, causes=(CAUSE_B,))]))

    assert "| 1 | pom.xml | 36 |" in block
    assert block.index("| 1 |") < block.index("| 2 |")


def test_the_outcome_is_stated_in_words():
    block = render(result([attempt(1, before=STATE_A, after=STATE_A)]))

    assert "same failure" in block


def test_a_state_change_is_shown_as_a_movement():
    block = render(result([attempt(1, before=STATE_A, after=STATE_B)]))

    assert "A -> B" in block


def test_a_resolved_attempt_is_shown_as_passing():
    block = render(result([attempt(1, resolved=True)]))

    assert "A -> passing" in block


def test_each_failure_state_is_described_exactly_once():
    """Three attempts against one failure describe it once, not three times."""
    block = render(result([attempt(index, after=STATE_A) for index in (1, 2, 3)]))

    assert block.count(CAUSE_A) == 1
    assert "- A: " + CAUSE_A in block


def test_a_cycle_is_called_out_rather_than_left_to_be_noticed():
    block = render(result([attempt(1, STATE_A, STATE_B), attempt(2, STATE_B, STATE_A, causes=(CAUSE_B,))]))

    assert "A" in block
    assert "cancelled each other out" in block


def test_a_file_rewritten_repeatedly_is_visible_as_a_count():
    block = render(result([attempt(1, after=STATE_A), attempt(2, after=STATE_A)]))

    assert "pom.xml (2)" in block


def test_omitted_earlier_attempts_are_reported():
    history = [attempt(index, after=STATE_A) for index in range(1, 4)]
    partial = RetrievalResult(
        loop_history=history,
        loop_summary=build_loop_summary(
            history, attempts_listed=2, testing_module="d365_http_client", testing_frid="1", suite="conformance"
        ),
    )

    assert "earlier attempt(s) not listed" in render(partial)


# --- diffs ------------------------------------------------------------------------


def test_only_the_most_recent_attempts_carry_their_diff():
    """The rest of the loop's changes are already in the code the reader can see."""
    history = [attempt(index, after=STATE_A, diff=f"--- f{index}.py\n+ change {index}") for index in range(1, 6)]

    block = render(result(history))

    assert block.count("```diff") == LOOP_DIFF_ATTEMPTS
    assert "+ change 5" in block
    assert "+ change 1" not in block


def test_a_confirmed_observation_elsewhere_carries_its_diff():
    """Its change is in another module, so naming the file alone says nothing actionable."""
    elsewhere = attempt(
        2, module="d365_version_api", resolved=True, diff="--- pom.xml\n+ <exclusion>org.slf4j</exclusion>"
    )

    block = render(result(associative=[elsewhere]))

    assert elsewhere.status == Status.VERIFIED.value
    assert "+ <exclusion>org.slf4j</exclusion>" in block


def test_a_ruled_out_observation_elsewhere_does_not():
    ruled_out = attempt(2, module="d365_version_api", after=STATE_A, diff="--- pom.xml\n+ something else")

    block = render(result(associative=[ruled_out]))

    assert "```diff" not in block
    assert "did not resolve" in block


# --- observations from elsewhere --------------------------------------------------


def test_an_observation_elsewhere_names_its_module_and_functionality():
    block = render(result(associative=[attempt(2, module="d365_version_api", frid="3", resolved=True)]))

    assert "d365_version_api / functionality 3" in block


def test_confirmed_and_ruled_out_are_separate_sections():
    block = render(
        result(
            associative=[
                attempt(1, module="other_a", resolved=True),
                attempt(2, module="other_b", after=STATE_A),
            ]
        )
    )

    assert block.index("Confirmed elsewhere") < block.index("Ruled out elsewhere")


def test_which_code_was_changed_is_stated_plainly():
    """`CONFORMANCE_TESTS` observes where the change landed, not that a test was weakened."""
    block = render(result(associative=[attempt(1, module="other", resolved=True)]))

    assert "conformance test project" in block


def test_a_repeated_observation_says_how_many_times():
    record = attempt(1, module="other", resolved=True)
    record.occurrences = 3

    assert "seen 3 times" in render(result(associative=[record]))


# --- economy ----------------------------------------------------------------------


def test_nothing_retrieved_renders_to_nothing():
    assert render(RetrievalResult()) == ""


def test_a_long_loop_stays_within_a_sane_token_budget():
    """Twelve attempts and three observations elsewhere, on realistic-length values."""
    history = [
        attempt(index, after=STATE_A if index % 2 else STATE_B, diff=f"--- src/main/java/App.java\n+ line {index}")
        for index in range(1, 13)
    ]
    elsewhere = [attempt(1, module=f"module_{index}", resolved=True) for index in range(3)]

    block = render(result(history, elsewhere))

    # Roughly four characters per token; the previous per-record JSON payload spent about
    # a thousand tokens on a single record.
    assert len(block) < 6000
