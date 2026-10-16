"""Tests for the reason a render gives when it stops on an error.

When a render stops, the reason is logged and shown in the TUI. These tests drive the
real render state machine through the last failing unit-test run of a functionality
and check that the stop reason names the cause, instead of the literal "None".
"""

import logging
import os
import stat
import tempfile
import threading
from types import SimpleNamespace

import pytest

import plain2code_logger
from event_bus import EventBus
from memory_management import MemoryManager
from plain2code_state import RunState
from plain_modules import PlainModule
from render_machine.actions.exit_with_error import ExitWithError
from render_machine.actions.render_functional_requirement import MAX_CODE_GENERATION_RETRIES
from render_machine.code_renderer import CodeRenderer
from render_machine.render_context import (
    MAX_FUNCTIONAL_REQUIREMENT_RENDER_ATTEMPTS_FAILED_UNIT_DURING_CONFORMANCE_TESTS,
    MAX_UNITTEST_FIX_ATTEMPTS,
    RenderContext,
)
from render_machine.render_types import RenderError, UnitTestsRunningContext
from render_machine.states import States


class FakeCodeplainAPI:
    def __init__(self):
        self.failed_frids = []

    def fail_functional_requirement(self, frid, module_name, run_state):
        self.failed_frids.append(frid)


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as folder:
        build_folder = os.path.join(folder, "plain_modules")
        os.makedirs(build_folder)

        unittests_script = os.path.join(folder, "run_unittests.sh")
        with open(unittests_script, "w", encoding="utf-8") as script:
            script.write("#!/bin/sh\necho '1 failed, 0 passed'\nexit 1\n")
        os.chmod(unittests_script, os.stat(unittests_script).st_mode | stat.S_IEXEC)

        yield build_folder, unittests_script


def make_render_context(api, build_folder, unittests_script, fixtures_dir):
    plain_module = PlainModule("pr_solo.plain", build_folder, [fixtures_dir])
    os.makedirs(plain_module.module_build_folder, exist_ok=True)
    return RenderContext(
        api,
        MemoryManager(api, plain_module.module_memory_folder),
        plain_module,
        build_folder=plain_module.module_build_folder,
        build_dest="dist",
        conformance_tests_dest="dist_tests",
        unittests_script=unittests_script,
        conformance_tests_script=None,
        prepare_environment_script=None,
        copy_build=False,
        copy_conformance_tests=False,
        render_range=None,
        render_conformance_tests=False,
        base_folder=fixtures_dir,
        run_state=RunState(spec_filename="pr_solo.plain"),
        event_bus=EventBus(),
        enter_pause_event=threading.Event(),
    )


def nested_state(*states: States) -> str:
    return "_".join(state.value for state in states)


def error_log_messages(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == plain2code_logger.LOGGER_NAME and record.levelno == logging.ERROR
    ]


@pytest.fixture
def fixtures_dir(get_test_data_path):
    return get_test_data_path("data/partial_rendering")


def test_unit_tests_failing_after_the_last_attempt_name_the_cause(workspace, fixtures_dir, caplog):
    build_folder, unittests_script = workspace
    api = FakeCodeplainAPI()
    render_context = make_render_context(api, build_folder, unittests_script, fixtures_dir)
    code_renderer = CodeRenderer(render_context)

    # The last render attempt of functionality 1, after its last unit-test fix round.
    render_context.start_implementing_frid()
    render_context.frid_context.functional_requirement_render_attempts = MAX_CODE_GENERATION_RETRIES
    render_context.unit_tests_running_context = UnitTestsRunningContext(fix_attempts=MAX_UNITTEST_FIX_ATTEMPTS)
    code_renderer.machine.set_state(
        nested_state(States.IMPLEMENTING_FRID, States.PROCESSING_UNIT_TESTS, States.UNIT_TESTS_READY)
    )

    with caplog.at_level(logging.DEBUG, logger=plain2code_logger.LOGGER_NAME):
        code_renderer.run()

    assert render_context.state == States.RENDER_FAILED.value
    assert api.failed_frids == ["1"]

    last_output_path = render_context.script_execution_history.latest_unit_test_output_path
    assert last_output_path is not None and os.path.exists(last_output_path)

    expected_message = (
        f"Functionality 1: unit tests still fail after {MAX_CODE_GENERATION_RETRIES} render attempts, "
        f"{MAX_UNITTEST_FIX_ATTEMPTS} fix rounds each. Last unit tests output: {last_output_path}"
    )
    assert error_log_messages(caplog) == [expected_message]
    # The TUI shows the same stop reason as the log.
    assert render_context.last_error_message == expected_message


def test_unit_tests_failing_again_during_conformance_tests_name_the_cause_once(workspace, fixtures_dir, caplog):
    build_folder, unittests_script = workspace
    api = FakeCodeplainAPI()
    render_context = make_render_context(api, build_folder, unittests_script, fixtures_dir)
    code_renderer = CodeRenderer(render_context)

    # Unit tests broken by a conformance fix, failing their last fix round for the last allowed time.
    render_context.start_implementing_frid()
    render_context.start_conformance_tests_processing()
    render_context.functional_requirements_render_attempts_failed_unit_during_conformance_tests = (
        MAX_FUNCTIONAL_REQUIREMENT_RENDER_ATTEMPTS_FAILED_UNIT_DURING_CONFORMANCE_TESTS - 1
    )
    render_context.unit_tests_running_context = UnitTestsRunningContext(fix_attempts=MAX_UNITTEST_FIX_ATTEMPTS)
    code_renderer.machine.set_state(
        nested_state(
            States.IMPLEMENTING_FRID,
            States.PROCESSING_CONFORMANCE_TESTS,
            States.PROCESSING_UNIT_TESTS,
            States.UNIT_TESTS_READY,
        )
    )

    with caplog.at_level(logging.DEBUG, logger=plain2code_logger.LOGGER_NAME):
        code_renderer.run()

    assert render_context.state == States.RENDER_FAILED.value
    error_messages = error_log_messages(caplog)
    assert len(error_messages) == 1
    assert error_messages[0].startswith("Failed to adjust the unit tests")
    assert "functionality 1" in error_messages[0]
    # The TUI shows the same stop reason as the log, not the unit tests output of the payload.
    assert render_context.last_error_message == error_messages[0]


class TestExitWithErrorMessage:
    @staticmethod
    def make_context(last_error_message=None):
        render_context = RenderContext.__new__(RenderContext)
        render_context.codeplain_api = FakeCodeplainAPI()
        render_context.frid_context = SimpleNamespace(frid="1")
        render_context.module_name = "mod"
        render_context.run_state = RunState(spec_filename="mod.plain")
        render_context.last_error_message = last_error_message
        return render_context

    def run_exit(self, render_context, payload, caplog):
        with caplog.at_level(logging.DEBUG, logger=plain2code_logger.LOGGER_NAME):
            ExitWithError().execute(render_context, payload)
        return error_log_messages(caplog)

    def test_logs_the_message_of_an_error_payload(self, caplog):
        render_context = self.make_context()
        payload = RenderError.encode(
            message="Unit tests script failed due to problems in the environment setup.",
            error_type="ENVIRONMENT_ERROR",
            script="run_unittests.sh",
        ).to_payload()

        messages = self.run_exit(render_context, payload, caplog)

        expected = (
            "Unit tests script failed due to problems in the environment setup.\n\nDetails:\n  Script: run_unittests.sh"
        )
        assert messages == [expected]
        assert render_context.last_error_message == expected

    def test_logs_the_stored_message_when_the_payload_is_not_an_error(self, caplog):
        render_context = self.make_context(last_error_message="Conformance tests could not be fixed.")

        messages = self.run_exit(render_context, {"previous_unittests_issue": "1 failed"}, caplog)

        assert messages == ["Conformance tests could not be fixed."]
        assert render_context.last_error_message == "Conformance tests could not be fixed."

    def test_never_logs_none(self, caplog):
        render_context = self.make_context()

        messages = self.run_exit(render_context, None, caplog)

        assert len(messages) == 1
        assert "None" not in messages[0]
        assert render_context.last_error_message == messages[0]
