import os
import threading
from types import SimpleNamespace
from unittest import mock

from render_machine.actions import prepare_testing_environment as pte


def _context(tmp_path, should_prepare=True):
    build = tmp_path / "code"
    build.mkdir()
    (build / "a.txt").write_text("a")
    return SimpleNamespace(
        prepare_environment_script="prepare.sh",
        build_folder=str(build),
        test_script_timeout=10,
        stop_event=threading.Event(),
        conformance_tests_running_context=SimpleNamespace(should_prepare_testing_environment=should_prepare),
        script_execution_history=SimpleNamespace(),
        background_preparation=None,
    )


def test_a_preparation_started_ahead_is_used_once_the_build_folder_is_unchanged(tmp_path):
    ctx = _context(tmp_path)
    with mock.patch.object(pte.render_utils, "execute_script", return_value=(0, "", "out")) as run:
        pte.start_in_background(ctx)
        outcome, _ = pte.PrepareTestingEnvironment().execute(ctx, None)
    assert outcome == pte.PrepareTestingEnvironment.SUCCESSFUL_OUTCOME
    assert run.call_count == 1 and ctx.background_preparation is None
    assert ctx.conformance_tests_running_context.should_prepare_testing_environment is False


def test_a_preparation_started_ahead_is_redone_when_the_build_folder_changed(tmp_path):
    ctx = _context(tmp_path)
    gate = threading.Event()

    def slow(*_args, **_kwargs):
        gate.wait(5)
        return (0, "", "out")

    with mock.patch.object(pte.render_utils, "execute_script", side_effect=slow) as run:
        pte.start_in_background(ctx)
        with open(os.path.join(ctx.build_folder, "b.txt"), "w") as f:
            f.write("b")
        gate.set()
        pte.PrepareTestingEnvironment().execute(ctx, None)
    assert run.call_count == 2


def test_nothing_is_started_when_no_preparation_is_due(tmp_path):
    ctx = _context(tmp_path, should_prepare=False)
    with mock.patch.object(pte.render_utils, "execute_script") as run:
        pte.start_in_background(ctx)
    assert ctx.background_preparation is None and run.call_count == 0


def test_a_failed_preparation_started_ahead_fails_the_action(tmp_path):
    ctx = _context(tmp_path)
    with mock.patch.object(pte.render_utils, "execute_script", return_value=(3, "", "out")):
        pte.start_in_background(ctx)
        outcome, _ = pte.PrepareTestingEnvironment().execute(ctx, None)
    assert outcome == pte.PrepareTestingEnvironment.FAILED_OUTCOME
