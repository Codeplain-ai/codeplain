"""Validation of the terminal contract, driven through `execute_script()`.

Everything here goes through the real path a render takes — `execute_script()` with a real
script on disk — rather than through a backend directly. The backend suites assert how the
pieces behave; this one asserts that what a rendered script actually observes matches the
contract: a terminal of its own on all three descriptors, its own session and foreground
process group, a bounded lifecycle, the documented process-tree limits, and an environment
with exactly the hints the renderer promises and no others.

Cases already covered verbatim elsewhere are not repeated here:

- output larger than the terminal buffer, exit-code passthrough, merged stderr, the
  timeout result with its partial output, and the timeout message naming the absent input
  driver live in `tests/test_render_utils.py`
- the escape hatch's selection rule, its warning, and the variable's absence from the
  child environment live in `tests/test_no_pty_escape_hatch.py`; what is added here is the
  terminal-isolation guard run through `execute_script()` against *both* backends

Scripts are executed for real, so the whole module is POSIX-only.
"""

import errno
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from plain2code_exceptions import RenderCancelledError
from render_machine import render_utils
from render_machine.terminal_process import (
    DEFAULT_TERM,
    ENVIRONMENT_ERROR_EXIT_CODE,
    NO_PTY_ENV_VAR,
)
from tests import test_render_utils as characterization

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="execute_script() runs .ps1 scripts on Windows; these cases use POSIX scripts.",
)

REPO_ROOT = str(Path(__file__).resolve().parent.parent)
SCRIPT_TYPE = characterization.SCRIPT_TYPE

NODE = shutil.which("node")
GIT = shutil.which("git")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed on this machine.")
needs_git = pytest.mark.skipif(GIT is None, reason="git is not installed on this machine.")

# Every wait below is bounded. The budgets are generous relative to the work they cover,
# so a failure means something hung rather than that the machine was busy.
SETTLE_SECONDS = 5.0
LIVENESS_WINDOW_SECONDS = 1.0
DETACHED_TIMEOUT_SECONDS = 60.0

_make_shell_script = characterization._make_shell_script
_make_python_script = characterization._make_python_script

# Fixtures reused from the characterization module: the same output-file bookkeeping and
# the same real PTY on the harness's own fd 0.
run_script = characterization.run_script
terminal_on_stdin = characterization.terminal_on_stdin


def _report(output):
    """Parses a JSON report out of a rendered transcript.

    The transcript is rendered from a 120-column screen, so a long report is wrapped
    across rows. Joining the rows restores it: the probes below emit JSON without
    insignificant whitespace, and rendering only ever drops trailing blanks.
    """
    return json.loads("".join(output.split("\n")))


def _wait_until(predicate, seconds):
    """Polls a predicate to a deadline and returns whether it ever held."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _open_descriptor_count():
    try:
        return len(os.listdir("/dev/fd"))
    except OSError as exc:  # pragma: no cover - only on a host without /dev/fd
        pytest.skip(f"open descriptors cannot be counted in this environment: {exc}")


# --- Terminal invariants ---------------------------------------------------------

INVARIANT_PROBE_PROGRAM = """
import json
import os
import sys

report = {
    "isatty_stdin": os.isatty(0),
    "isatty_stdout": os.isatty(1),
    "isatty_stderr": os.isatty(2),
    "session_leader": os.getsid(0) == os.getpid(),
    "group_leader": os.getpgrp() == os.getpid(),
    "in_the_foreground": os.tcgetpgrp(0) == os.getpgrp(),
    "dev_tty": False,
    "term": os.environ.get("TERM"),
}
try:
    tty_fd = os.open("/dev/tty", os.O_RDWR)
except OSError:
    pass
else:
    report["dev_tty"] = True
    os.close(tty_fd)
sys.stdout.write(json.dumps(report, separators=(",", ":")))
sys.stdout.flush()
"""


def test_a_script_leads_its_own_session_with_its_terminal_in_the_foreground(tmp_path, run_script):
    """The whole topology asserted from inside the rendered command, in one run."""
    script = _make_python_script(tmp_path, "invariants", INVARIANT_PROBE_PROGRAM)

    exit_code, output, _ = run_script(script, [], SCRIPT_TYPE, timeout=30)

    assert exit_code == 0
    report = _report(output)
    assert report.pop("term")  # asserted in full by the child-environment cases below
    assert report == {
        "isatty_stdin": True,
        "isatty_stdout": True,
        "isatty_stderr": True,
        "session_leader": True,
        "group_leader": True,
        "in_the_foreground": True,
        "dev_tty": True,
    }


# --- Compatibility ---------------------------------------------------------------


@needs_node
def test_node_reports_its_version_through_the_real_path(run_script):
    """Node is resolved to an absolute path: a bare name would be rewritten to `./node`."""
    exit_code, output, _ = run_script(NODE, ["--version"], SCRIPT_TYPE, timeout=60)

    assert exit_code == 0
    assert re.match(r"^v\d+\.\d+\.\d+", output.strip()), output


def test_a_shell_sees_a_terminal_on_all_three_descriptors(tmp_path, run_script):
    script = _make_shell_script(
        tmp_path,
        "shell_tty",
        'test -t 0 && test -t 1 && test -t 2 && echo "all three are terminals"\n',
    )

    exit_code, output, _ = run_script(script, [], SCRIPT_TYPE, timeout=30)

    assert exit_code == 0
    assert "all three are terminals" in output


NODE_RAW_MODE_PROGRAM = """
const report = {isTTY: process.stdin.isTTY === true, raw: false, restored: false};
try {
  process.stdin.setRawMode(true);
  report.raw = process.stdin.isRaw === true;
  process.stdin.setRawMode(false);
  report.restored = process.stdin.isRaw === false;
} catch (error) {
  report.error = String(error.message).replace(/\\s+/g, "-");
}
process.stdout.write(JSON.stringify(report));
process.exit(0);
"""


@needs_node
def test_node_sees_a_tty_on_stdin_and_can_toggle_raw_mode(tmp_path, run_script):
    script_path = tmp_path / "raw_mode.js"
    script_path.write_text(f"#!{NODE}\n" + textwrap.dedent(NODE_RAW_MODE_PROGRAM))
    script_path.chmod(0o755)

    exit_code, output, _ = run_script(str(script_path), [], SCRIPT_TYPE, timeout=60)

    assert exit_code == 0
    assert _report(output) == {"isTTY": True, "raw": True, "restored": True}


TERMIOS_MODE_PROBE_PROGRAM = """
import json
import os
import sys
import termios
import tty

saved = termios.tcgetattr(0)
report = {"canonical": bool(termios.tcgetattr(0)[3] & termios.ICANON)}
try:
    tty.setcbreak(0)
    report["cbreak"] = not termios.tcgetattr(0)[3] & termios.ICANON
    tty.setraw(0)
    local = termios.tcgetattr(0)[3]
    report["raw"] = not local & (termios.ICANON | termios.ECHO | termios.ISIG)
finally:
    termios.tcsetattr(0, termios.TCSANOW, saved)
report["restored"] = bool(termios.tcgetattr(0)[3] & termios.ICANON)
sys.stdout.write(json.dumps(report, separators=(",", ":")))
sys.stdout.flush()
"""


def test_canonical_cbreak_and_raw_modes_are_all_reachable(tmp_path, run_script):
    script = _make_python_script(tmp_path, "termios_modes", TERMIOS_MODE_PROBE_PROGRAM)

    exit_code, output, _ = run_script(script, [], SCRIPT_TYPE, timeout=30)

    assert exit_code == 0
    assert _report(output) == {"canonical": True, "cbreak": True, "raw": True, "restored": True}


FRAGMENTED_OUTPUT_PROGRAM = """
import sys
import time

# One byte per write, so every multi-byte character crosses a read boundary.
for byte in "hello wörld ✓ café".encode():
    sys.stdout.buffer.write(bytes([byte]))
    sys.stdout.buffer.flush()
    time.sleep(0.001)
sys.stdout.buffer.write(b"\\n")
sys.stdout.buffer.flush()
# The same for an SGR sequence, which the renderer must consume rather than print.
for chunk in (b"\\x1b", b"[3", b"1m", b"red text", b"\\x1b", b"[0", b"m\\n"):
    sys.stdout.buffer.write(chunk)
    sys.stdout.buffer.flush()
    time.sleep(0.001)
"""


def test_partial_utf8_and_split_escape_sequences_survive_the_stream(tmp_path, run_script):
    script = _make_python_script(tmp_path, "fragmented", FRAGMENTED_OUTPUT_PROGRAM)

    exit_code, output, _ = run_script(script, [], SCRIPT_TYPE, timeout=30)

    assert exit_code == 0
    assert "hello wörld ✓ café" in output
    assert "red text" in output
    assert "\033[" not in output
    assert "�" not in output  # no replacement character from a split code point


# --- Lifecycle -------------------------------------------------------------------


def test_a_stop_event_set_mid_run_cancels_the_script(tmp_path):
    """Cancellation while the target is running, rather than before it starts."""
    script = _make_shell_script(tmp_path, "long_run", 'echo "started"\nsleep 30\n')
    stop_event = threading.Event()
    canceller = threading.Timer(1.0, stop_event.set)
    canceller.start()

    started = time.monotonic()
    try:
        with pytest.raises(RenderCancelledError):
            render_utils.execute_script(script, [], SCRIPT_TYPE, timeout=30, stop_event=stop_event)
    finally:
        canceller.cancel()

    assert time.monotonic() - started < 20


def test_repeated_executions_leak_no_descriptors_and_no_threads(tmp_path, run_script):
    script = _make_shell_script(tmp_path, "quick", 'echo "done"\n')
    run_script(script, [], SCRIPT_TYPE, timeout=30)  # first run pays the import costs

    descriptors_before = _open_descriptor_count()
    threads_before = threading.active_count()
    for _ in range(4):
        exit_code, output, _ = run_script(script, [], SCRIPT_TYPE, timeout=30)
        assert exit_code == 0
        assert "done" in output

    # The reaper is a background thread on the teardown path, so both counts are given a
    # bounded moment to return to where they started.
    assert _wait_until(lambda: _open_descriptor_count() <= descriptors_before, SETTLE_SECONDS)
    assert _wait_until(lambda: threading.active_count() <= threads_before, SETTLE_SECONDS)


# --- Process-tree boundaries -----------------------------------------------------
#
# These assert the *documented* contract, not full containment: a descendant that leaves
# the process group, and one whose leader was reaped before teardown, are outside what
# `terminate_tree()` claims to reach. Both are cases Phase 6's Job Object does contain,
# which is the platform asymmetry these cases exist to keep visible.

DESCENDANT_PROGRAM = """
import os
import signal
import sys
import time

beats_path, mode = sys.argv[1], sys.argv[2]
pid = os.fork()
if pid == 0:
    if "own-group" in mode:
        os.setpgid(0, 0)
    if "ignore-hup" in mode:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        with open(beats_path, "a") as beats:
            beats.write("tick\\n")
        time.sleep(0.05)
    os._exit(0)
sys.stdout.write("descendant %d\\n" % pid)
sys.stdout.flush()
if "leader-exits" in mode:
    sys.exit(0)
time.sleep(60)
"""


@pytest.fixture
def descendants():
    """Kills whatever a case deliberately left running outside the process tree."""
    survivors = []
    yield survivors
    for pid in survivors:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def _descendant_pid(output):
    match = re.search(r"descendant (\d+)", output)
    assert match is not None, f"the script never reported its descendant: {output!r}"
    return int(match.group(1))


def _beats(path):
    try:
        return path.read_text().count("tick")
    except OSError:
        return 0


def _still_beating(path):
    """True when the heartbeat file grows over a bounded window."""
    before = _beats(path)
    return _wait_until(lambda: _beats(path) > before, LIVENESS_WINDOW_SECONDS)


def test_a_descendant_that_leaves_the_process_group_survives_termination(tmp_path, run_script, descendants):
    beats = tmp_path / "own_group.beats"
    script = _make_python_script(tmp_path, "escapes_group", DESCENDANT_PROGRAM)

    exit_code, output, _ = run_script(script, [str(beats), "own-group"], SCRIPT_TYPE, timeout=2)

    assert exit_code == render_utils.TIMEOUT_ERROR_EXIT_CODE
    descendants.append(_descendant_pid(output))
    assert _still_beating(beats), "the documented escape stopped working: the descendant was reached after all"


def test_a_sighup_ignoring_descendant_survives_a_leader_reaped_before_teardown(tmp_path, run_script, descendants):
    """Once `poll()` has reaped the leader the pgid may be recycled, so nothing is signalled."""
    beats = tmp_path / "same_group.beats"
    script = _make_python_script(tmp_path, "leader_exits", DESCENDANT_PROGRAM)

    exit_code, output, _ = run_script(script, [str(beats), "ignore-hup,leader-exits"], SCRIPT_TYPE, timeout=30)

    assert exit_code == 0
    descendants.append(_descendant_pid(output))
    assert _still_beating(beats)


HANGUP_SCRIPT_PROGRAM = """
import os
import signal
import sys
import time

directory = sys.argv[1]
for name, ignores_hup in (("default", False), ("ignoring", True)):
    if os.fork() == 0:
        if ignores_hup:
            signal.signal(signal.SIGHUP, signal.SIG_IGN)
        with open(os.path.join(directory, name + ".pid"), "w") as pid_file:
            pid_file.write(str(os.getpid()))
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            with open(os.path.join(directory, name + ".beats"), "a") as beats:
                beats.write("tick\\n")
            time.sleep(0.05)
        os._exit(0)
time.sleep(60)
"""


def test_a_dead_renderer_hangs_up_the_terminal_but_cannot_contain_the_tree(tmp_path, descendants):
    """Best-effort, and deliberately asserted as such.

    When Codeplain itself dies the master closes, the slave hangs up, and the foreground
    group receives `SIGHUP` — which terminates a default-disposition descendant and does
    nothing at all to one that ignores it. Genuine crash containment needs an OS mechanism
    that outlives the renderer, which is not what this path provides.
    """
    script = _make_python_script(tmp_path, "hangup_targets", HANGUP_SCRIPT_PROGRAM)
    runner = subprocess.Popen(
        [sys.executable, "-c", _renderer_program(script, [str(tmp_path)])],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(tmp_path),
        start_new_session=True,
    )
    default_beats, ignoring_beats = tmp_path / "default.beats", tmp_path / "ignoring.beats"
    try:
        assert _wait_until(lambda: _beats(default_beats) and _beats(ignoring_beats), 30.0), "descendants never started"
        for name in ("default", "ignoring"):
            descendants.append(int((tmp_path / f"{name}.pid").read_text()))
        runner.kill()  # the renderer dies without ever running its teardown
        runner.wait(timeout=SETTLE_SECONDS)
    finally:
        if runner.poll() is None:  # pragma: no cover - only if the kill above never landed
            runner.kill()

    assert _wait_until(lambda: not _still_beating(default_beats), SETTLE_SECONDS)
    assert _still_beating(ignoring_beats), "a SIGHUP-ignoring descendant is expected to survive the hangup"


def _renderer_program(script, args, result_path=None):
    """A one-liner renderer: imports the real path and runs one script through it."""
    return textwrap.dedent(f"""
        import json
        import os
        import sys

        sys.path.insert(0, {REPO_ROOT!r})
        from render_machine import render_utils

        renderer_stdin_is_a_terminal = os.isatty(0)
        exit_code, output, _ = render_utils.execute_script(
            {script!r}, {args!r}, "Detached", timeout={int(DETACHED_TIMEOUT_SECONDS)}
        )
        result_path = {result_path!r}
        if result_path is not None:
            with open(result_path, "w") as result_file:
                json.dump(
                    {{
                        "exit_code": exit_code,
                        "output": output,
                        "renderer_stdin_is_a_terminal": renderer_stdin_is_a_terminal,
                    }},
                    result_file,
                )
        """)


# --- PTY exhaustion --------------------------------------------------------------


def _never_constructed(*_args, **_kwargs):
    raise AssertionError("the pipe backend was constructed as a fallback")


def test_a_failed_openpty_reports_the_errno_and_never_falls_back_to_pipes(tmp_path, run_script, monkeypatch):
    """PTYs are a finite system resource; running out of them is an environment failure."""
    script = _make_shell_script(tmp_path, "never_runs", 'echo "unreachable"\n')
    monkeypatch.delenv(NO_PTY_ENV_VAR, raising=False)

    def exhausted(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    monkeypatch.setattr("render_machine._posix_pty.os.openpty", exhausted)
    monkeypatch.setattr("render_machine._legacy_pipe.LegacyPipeProcess", _never_constructed)

    exit_code, issue, _ = run_script(script, [], SCRIPT_TYPE, timeout=30)

    assert exit_code == ENVIRONMENT_ERROR_EXIT_CODE
    assert "pseudoterminal" in issue
    assert f"Errno {errno.ENOSPC}" in issue
    assert "unreachable" not in issue


# --- Terminal isolation (F7) -----------------------------------------------------


@pytest.mark.parametrize("pty_disabled", [False, True], ids=["pty backend", "pipe backend"])
def test_a_script_never_reads_the_renderers_terminal_on_either_backend(
    tmp_path, run_script, terminal_on_stdin, monkeypatch, pty_disabled
):
    """The decisive case: the harness holds a real terminal and types into it.

    Both backends have to keep the script away from it — the escape hatch and the Windows
    interim must not reopen the hole the PTY closes.
    """
    if pty_disabled:
        monkeypatch.setenv(NO_PTY_ENV_VAR, "1")
    else:
        monkeypatch.delenv(NO_PTY_ENV_VAR, raising=False)
    script = _make_python_script(tmp_path, "stdin_probe", characterization.STDIN_PROBE_PROGRAM)
    os.write(terminal_on_stdin, characterization.KEYSTROKES.encode())

    exit_code, output, _ = run_script(script, [], SCRIPT_TYPE, timeout=30)

    assert exit_code == 0
    report = _report(output)
    assert report["data"] == ""
    assert report["isatty"] is not pty_disabled  # a terminal of its own, or none at all
    assert characterization.KEYSTROKES.strip() not in output


# --- The no-input contract -------------------------------------------------------
#
# The repeatedly-reading case — the timeout message naming the absent input driver — is
# asserted in `tests/test_render_utils.py` and is not repeated here.

SINGLE_READ_PROGRAM = """
import os
import sys
import time

started = time.monotonic()
data = os.read(0, 1024)
sys.stdout.write("read %d bytes in %.2fs\\n" % (len(data), time.monotonic() - started))
sys.stdout.flush()
"""


def test_a_single_read_script_exits_on_the_spawn_time_veof(tmp_path, run_script):
    script = _make_python_script(tmp_path, "single_read", SINGLE_READ_PROGRAM)

    started = time.monotonic()
    exit_code, output, _ = run_script(script, [], SCRIPT_TYPE, timeout=30)
    elapsed = time.monotonic() - started

    assert exit_code == 0
    assert "read 0 bytes" in output
    assert elapsed < 20  # nowhere near the configured timeout


def test_a_slow_silent_script_that_never_reads_completes_untouched(tmp_path, run_script):
    script = _make_shell_script(tmp_path, "slow_and_silent", 'sleep 3\necho "finished on its own"\n')

    started = time.monotonic()
    exit_code, output, _ = run_script(script, [], SCRIPT_TYPE, timeout=30)
    elapsed = time.monotonic() - started

    assert exit_code == 0
    assert "finished on its own" in output
    assert elapsed >= 3


# --- The child environment -------------------------------------------------------

# Hints a non-interactive runner might be tempted to set. The child gets a terminal, so
# none of them belongs in its environment.
NON_INTERACTIVE_HINTS = ("CI", "PIP_NO_INPUT", "NPM_CONFIG_YES", "DEBIAN_FRONTEND")

ENVIRONMENT_PROBE_PROGRAM = """
import json
import os
import sys

names = ("TERM", "GIT_TERMINAL_PROMPT", "CI", "PIP_NO_INPUT", "NPM_CONFIG_YES", "DEBIAN_FRONTEND")
sys.stdout.write(json.dumps({name: os.environ.get(name) for name in names}, separators=(",", ":")))
sys.stdout.flush()
"""


@pytest.fixture
def child_environment(tmp_path, run_script):
    """Runs the environment probe and returns what the child saw."""
    script = _make_python_script(tmp_path, "env_probe", ENVIRONMENT_PROBE_PROGRAM)

    def _run():
        exit_code, output, _ = run_script(script, [], SCRIPT_TYPE, timeout=30)
        assert exit_code == 0
        return _report(output)

    return _run


@pytest.mark.parametrize(
    "parent_term, expected",
    [("vt100-under-test", "vt100-under-test"), (None, DEFAULT_TERM), ("", DEFAULT_TERM)],
    ids=["inherited when set", "defaulted when unset", "defaulted when detached-empty"],
)
def test_term_is_inherited_when_set_and_defaulted_otherwise(monkeypatch, child_environment, parent_term, expected):
    if parent_term is None:
        monkeypatch.delenv("TERM", raising=False)
    else:
        monkeypatch.setenv("TERM", parent_term)

    assert child_environment()["TERM"] == expected


def test_git_terminal_prompt_reaches_the_child_even_when_the_parent_disagrees(monkeypatch, child_environment):
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")

    assert child_environment()["GIT_TERMINAL_PROMPT"] == "0"


def test_no_other_non_interactive_hint_reaches_the_child(monkeypatch, child_environment):
    for name in NON_INTERACTIVE_HINTS:
        monkeypatch.delenv(name, raising=False)

    seen = child_environment()

    assert {name: seen[name] for name in NON_INTERACTIVE_HINTS} == dict.fromkeys(NON_INTERACTIVE_HINTS, None)


class _UnauthorizedHandler(BaseHTTPRequestHandler):
    """Answers every request with a basic-auth challenge and nothing else."""

    def do_GET(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="git"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def credential_demanding_remote():
    """A local HTTP remote that demands credentials, so no network is involved."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _UnauthorizedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/repository.git"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=SETTLE_SECONDS)


@needs_git
def test_a_git_operation_needing_credentials_fails_instead_of_blocking_on_dev_tty(
    tmp_path, run_script, monkeypatch, credential_demanding_remote
):
    """git reads `/dev/tty` directly, so failing fast is the only bounded outcome."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "absent.gitconfig"))
    for name in ("GIT_ASKPASS", "SSH_ASKPASS", "GIT_CREDENTIAL_HELPER"):
        monkeypatch.delenv(name, raising=False)
    script = _make_shell_script(tmp_path, "needs_credentials", f'exec git ls-remote "{credential_demanding_remote}"\n')

    started = time.monotonic()
    exit_code, output, _ = run_script(script, [], SCRIPT_TYPE, timeout=30)
    elapsed = time.monotonic() - started

    assert exit_code not in (0, render_utils.TIMEOUT_ERROR_EXIT_CODE)
    assert "terminal prompts disabled" in output
    assert elapsed < 20


# --- Detached ---------------------------------------------------------------------


def test_a_detached_renderer_still_gives_the_script_a_terminal(tmp_path):
    """A `nohup`-style parent: its own session, no terminal anywhere, stdio redirected.

    This is the `execute_script()` half of the detached case. The full `--headless` render
    under `nohup` needs the live API, so it belongs to the e2e job rather than here.
    """
    script = _make_python_script(tmp_path, "detached_invariants", INVARIANT_PROBE_PROGRAM)
    result_path = tmp_path / "detached.json"
    log_path = tmp_path / "detached.log"
    environment = dict(os.environ)
    environment.pop("TERM", None)  # a detached parent commonly has none

    with open(log_path, "w") as log_file:
        runner = subprocess.Popen(
            [sys.executable, "-c", _renderer_program(script, [], str(result_path))],
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            cwd=str(tmp_path),
            env=environment,
            start_new_session=True,
        )
    try:
        assert runner.wait(timeout=DETACHED_TIMEOUT_SECONDS) == 0, log_path.read_text()
    finally:
        if runner.poll() is None:  # pragma: no cover - only if the detached run hung
            runner.kill()

    result = json.loads(result_path.read_text())
    assert result["exit_code"] == 0
    assert result["renderer_stdin_is_a_terminal"] is False
    report = _report(result["output"])
    assert report["isatty_stdin"] and report["isatty_stdout"] and report["isatty_stderr"]
    assert report["session_leader"] and report["in_the_foreground"]
    assert report["term"] == DEFAULT_TERM  # the detached parent had none to inherit
