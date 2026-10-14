"""How a render behaves with and without a terminal attached.

The TUI needs a person at a terminal: it reads keys from stdin, draws on stderr,
and the console summary goes to stdout. Started from a script or an agent
(`codeplain module.plain > render.log 2>&1 &`), stdin is at end-of-file.
Textual's input thread then reads EOF in a tight loop - millions of reads a
second - so the process pins a CPU core for the whole render, and a stopped
render waits for an Enter nobody can press. One such render stayed alive for
an hour and a half at ~100% CPU until it was killed.

So without a terminal codeplain runs as --headless does, and a stopped render
exits non-zero. With a terminal the TUI is kept, and waiting for Enter must
stay idle.
"""

import fcntl
import os
import struct
import subprocess
import sys
import termios
import textwrap
import threading
import time
from argparse import Namespace
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

import plain2code

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeStream:
    def __init__(self, is_terminal: bool):
        self._is_terminal = is_terminal

    def isatty(self) -> bool:
        return self._is_terminal


class TestAttachedToTerminal:
    @pytest.fixture
    def streams(self, monkeypatch):
        def set_streams(stdin=True, stdout=True, stderr=True):
            for name, value in (("stdin", stdin), ("stdout", stdout), ("stderr", stderr)):
                monkeypatch.setattr(sys, name, value if value is None else FakeStream(value))

        return set_streams

    def test_all_three_streams_on_a_terminal(self, streams):
        streams()

        assert plain2code.attached_to_terminal() is True

    @pytest.mark.parametrize("stream", ["stdin", "stdout", "stderr"])
    def test_any_stream_off_the_terminal(self, streams, stream):
        """stdin carries the keys, stderr the TUI, stdout the summary. Losing any
        of them leaves a TUI nobody can drive or see."""
        streams(**{stream: False})

        assert plain2code.attached_to_terminal() is False

    def test_a_closed_stream(self, streams):
        """sys.stdin is None when the process has no stdin at all."""
        streams(stdin=None)

        assert plain2code.attached_to_terminal() is False


def make_args():
    return Namespace(
        version=False,
        status=False,
        full_plain=False,
        dry_run=False,
        api_key="test-key",
        api="https://api.example.com",
        filename="project.plain",
        template_dir=None,
        build_folder="build",
        render_range=None,
        render_from=None,
        force_render=False,
        headless=False,
        replay_with=None,
        log_to_file=False,
        log_file_name="codeplain.log",
        render_conformance_tests=False,
        conformance_tests_script=None,
        unittests_script=None,
        prepare_environment_script=None,
    )


def run_render(succeeded, terminal, render_state=None, render_choices=None):
    """Drive the real main() and render() up to the module renderer.

    The module renderer stands in for the state machine: it decides how the
    render ended. Everything the decision under test does not touch - the API,
    parsing, logging, telemetry - is stubbed.

    Returns the mocks for the two TUIs and the module renderer, the args main()
    rendered with, and the exit status it asked for (None when it returned
    normally).
    """
    args = make_args()
    module_renderer = MagicMock()

    def make_module_renderer(codeplainAPI, plain_module, render_choice, render_range, args, run_state, *rest, **kw):
        module_renderer.render_module.side_effect = lambda: run_state.set_render_succeeded(succeeded)
        return module_renderer

    progress_tui = MagicMock()
    progress_tui.return_value.run.side_effect = lambda: progress_tui.call_args.kwargs["on_ready"]()
    choice_tui = MagicMock()

    patches = [
        patch("plain2code.attached_to_terminal", return_value=terminal),
        patch("plain2code.parse_arguments", return_value=args),
        patch("plain2code.file_utils.get_template_directories", return_value=["templates"]),
        patch("plain2code.plain_modules.PlainModule", return_value=MagicMock(all_required_modules=[])),
        patch("plain2code.setup_logging", return_value="INFO"),
        patch("plain2code.initialize_telemetry"),
        patch("plain2code.stop_narrating"),
        patch("plain2code.print_exit_summary"),
        patch("plain2code.dump_crash_logs"),
        patch("plain2code.capture_crash"),
        patch("plain2code.codeplain_api.CodeplainAPI"),
        patch("plain2code._check_connection", return_value="user@codeplain.ai"),
        patch("plain2code.signal.signal"),
        patch("plain2code.warn_if_acceptance_tests_without_conformance_script"),
        patch("plain2code.get_plain_module_render_state", return_value=render_state),
        patch("plain2code.get_render_choices", return_value=render_choices),
        patch("plain2code.ModuleRenderer", side_effect=make_module_renderer),
        patch("plain2code.Plain2CodeTUI", progress_tui),
        patch("plain2code.PlainModuleRenderChoiceTUI", choice_tui),
        patch.object(plain2code.console, "quiet", plain2code.console.quiet),
    ]

    exit_status = None
    with ExitStack() as stack:
        for each in patches:
            stack.enter_context(each)
        try:
            plain2code.main()
        except SystemExit as e:
            exit_status = e.code

    return Namespace(
        progress_tui=progress_tui,
        choice_tui=choice_tui,
        module_renderer=module_renderer,
        exit_status=exit_status,
        args=args,
    )


class TestRenderWithoutATerminal:
    def test_the_tui_never_starts(self):
        result = run_render(succeeded=False, terminal=False)

        result.progress_tui.assert_not_called()
        result.module_renderer.render_module.assert_called_once()

    def test_a_stopped_render_exits_non_zero(self):
        """The status --headless gives, so a script can tell it stopped."""
        result = run_render(succeeded=False, terminal=False)

        assert result.exit_status == 1

    def test_a_completed_render_exits_cleanly(self):
        result = run_render(succeeded=True, terminal=False)

        assert result.exit_status is None

    def test_says_why_it_runs_without_the_tui(self, caplog):
        caplog.set_level("INFO", logger="codeplain")

        run_render(succeeded=True, terminal=False)

        assert "No terminal attached" in caplog.text

    def test_never_asks_where_to_resume(self):
        """The choice prompt is a TUI too, and would wait for a key just the same."""
        destructive = MagicMock(is_destructive=True)
        choices = {"restart": destructive, "other": MagicMock(), "quit": MagicMock()}

        result = run_render(succeeded=True, terminal=False, render_state=MagicMock(), render_choices=choices)

        result.choice_tui.assert_not_called()


class TestRenderWithATerminal:
    def test_the_tui_still_runs(self):
        result = run_render(succeeded=True, terminal=True)

        result.progress_tui.assert_called_once()
        assert result.args.headless is False

    def test_says_nothing_about_a_missing_terminal(self, caplog):
        caplog.set_level("INFO", logger="codeplain")

        run_render(succeeded=True, terminal=True)

        assert "No terminal attached" not in caplog.text


IDLE_SECONDS = 2.0

# Drives the real TUI to the stopped state, then measures the CPU the process
# spends over IDLE_SECONDS while it waits for Enter.
STOPPED_TUI = textwrap.dedent("""
    import os, sys, threading, time

    from event_bus import EventBus
    from plain2code_events import RenderFailed
    from plain2code_state import RunState
    from tui.plain2code_tui import Plain2CodeTUI

    report_path, idle_seconds = sys.argv[1], float(sys.argv[2])
    event_bus = EventBus()

    def stop_then_measure():
        event_bus.publish(RenderFailed(error_message="unit tests still failing"))
        start = time.process_time()
        time.sleep(idle_seconds)
        with open(report_path + ".tmp", "w") as report:
            report.write(str(time.process_time() - start))
        os.replace(report_path + ".tmp", report_path)

    Plain2CodeTUI(
        event_bus=event_bus,
        run_state=RunState(spec_filename="x.plain"),
        on_ready=threading.Thread(target=stop_then_measure, daemon=True).start,
        render_id="test",
        unittests_script=None,
        conformance_tests_script=None,
        prepare_environment_script=None,
        state_machine_version="0.0.0",
        css_path="styles.css",
    ).run()
    """)


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pseudo-terminal")
def test_a_stopped_render_on_a_terminal_waits_for_enter_without_spinning(tmp_path):
    controller, terminal = os.openpty()
    fcntl.ioctl(terminal, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
    report = tmp_path / "cpu_seconds"

    process = subprocess.Popen(
        [sys.executable, "-c", STOPPED_TUI, str(report), str(IDLE_SECONDS)],
        stdin=terminal,
        stdout=terminal,
        stderr=terminal,
        cwd=REPO_ROOT,
        env={**os.environ, "TERM": "xterm-256color"},
        start_new_session=True,
    )
    os.close(terminal)

    def drain():
        # The TUI blocks once the terminal buffer fills, so keep reading it.
        try:
            while os.read(controller, 65536):
                pass
        except OSError:
            pass

    threading.Thread(target=drain, daemon=True).start()

    try:
        deadline = time.monotonic() + 60
        while not report.exists():
            assert process.poll() is None, "the TUI exited before Enter was pressed"
            assert time.monotonic() < deadline, "the TUI never reached the stopped state"
            time.sleep(0.1)
        cpu_seconds = float(report.read_text())

        os.write(controller, b"\r")
        exit_status = process.wait(timeout=15)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(controller)

    # A busy loop burns about one CPU-second per second of waiting.
    assert cpu_seconds < IDLE_SECONDS / 4
    assert exit_status == 0
