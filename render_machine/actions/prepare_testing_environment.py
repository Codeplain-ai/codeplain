import os
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

import render_machine.render_utils as render_utils
from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.render_context import RenderContext
from render_machine.render_types import RenderError


def _build_folder_signature(build_folder: str) -> tuple:
    """What the preparation script reads: the files of the build folder (outside .git), by size and change time."""
    entries = []
    for root, dirs, files in os.walk(build_folder):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in files:
            path = os.path.join(root, name)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            entries.append((os.path.relpath(path, build_folder), stat.st_size, stat.st_mtime_ns))
    return tuple(sorted(entries))


@dataclass
class BackgroundPreparation:
    """A run of the preparation script started ahead of time, and the build folder it prepared (as the script left
    it once it finished)."""

    signature: tuple
    done: threading.Event = field(default_factory=threading.Event)
    result: Optional[tuple[int, str, Optional[str]]] = None
    error: Optional[BaseException] = None


def _should_prepare(render_context: RenderContext) -> bool:
    return (
        render_context.prepare_environment_script is not None
        and render_context.conformance_tests_running_context is not None
        and render_context.conformance_tests_running_context.should_prepare_testing_environment
    )


def _run_script(render_context: RenderContext) -> tuple[int, str, Optional[str]]:
    console.info(
        f"Running testing environment preparation script {render_context.prepare_environment_script} for build folder {render_context.build_folder}."
    )
    return render_utils.execute_script(
        render_context.prepare_environment_script,
        [render_context.build_folder],
        "Testing Environment Preparation",
        timeout=render_context.test_script_timeout,
        stop_event=render_context.stop_event,
    )


def start_in_background(render_context: RenderContext) -> None:
    """Start the preparation script now when it will be due, so it runs while the conformance tests are being
    generated: it reads only the build folder, which the generation does not change. PrepareTestingEnvironment
    uses its result if the build folder is still the same, and runs the script again otherwise."""
    if not _should_prepare(render_context) or render_context.background_preparation is not None:
        return
    preparation = BackgroundPreparation(signature=_build_folder_signature(render_context.build_folder))

    def run() -> None:
        try:
            preparation.result = _run_script(render_context)
            # The script may write into the build folder (node_modules, target/, ...): compare against the folder
            # as it left it, or its own output would always look like a change and the script would run again.
            preparation.signature = _build_folder_signature(render_context.build_folder)
        except BaseException as e:  # noqa: B036 - handed to the waiting action
            preparation.error = e
        finally:
            preparation.done.set()

    render_context.background_preparation = preparation
    threading.Thread(target=run, name="prepare-testing-environment", daemon=True).start()


class PrepareTestingEnvironment(BaseAction):
    SUCCESSFUL_OUTCOME = "testing_environment_prepared"
    FAILED_OUTCOME = "testing_environment_preparation_failed"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        preparation = render_context.background_preparation
        render_context.background_preparation = None
        if not _should_prepare(render_context):
            if preparation is not None:
                preparation.done.wait()
            return self.SUCCESSFUL_OUTCOME, None

        result = None
        if preparation is not None:
            preparation.done.wait()
            if preparation.error is not None:
                raise preparation.error
            if preparation.signature == _build_folder_signature(render_context.build_folder):
                result = preparation.result
            else:
                console.debug("The build folder changed while the environment was being prepared; preparing again.")
        exit_code, _, preparation_temp_file_path = result if result is not None else _run_script(render_context)

        render_context.conformance_tests_running_context.should_prepare_testing_environment = False
        render_context.script_execution_history.latest_testing_environment_output_path = preparation_temp_file_path
        render_context.script_execution_history.should_update_script_outputs = True
        if exit_code == 0:
            return self.SUCCESSFUL_OUTCOME, None
        else:
            return (
                self.FAILED_OUTCOME,
                RenderError.encode(
                    message="Testing environment preparation failed. Please check the preparation script.",
                    error_type="ENVIRONMENT_ERROR",
                    exit_code=exit_code,
                    script=render_context.prepare_environment_script,
                ).to_payload(),
            )
