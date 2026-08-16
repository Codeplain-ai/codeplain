"""The ConPTY backend on native Windows.

Every case here allocates real pseudoconsoles, jobs, pipes and processes, so the whole
module is Windows-only and each helper is responsible for leaving nothing behind. The
fault-injection cases fail one native step at a time and assert what the rollback releases:
no process, no thread, no handle, and — on the paths that reach it — no pseudoconsole
closed on the foreground thread.

The teardown-completes-within-a-bound assertions are only meaningful on a build in the
range where `ClosePseudoConsole()` can block, which is why CI runs this module on a
`windows-2022` image as well as on `windows-latest`.
"""

import ctypes
import os
import signal
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

if sys.platform != "win32":
    # The module binds kernel32 at import time, so collection has to stop here rather than
    # leaving the cases to a skip mark.
    pytest.skip("The ConPTY backend is not built off Windows.", allow_module_level=True)

from ctypes import wintypes  # noqa: E402

from plain2code_exceptions import RenderCancelledError  # noqa: E402
from render_machine import _conpty  # noqa: E402
from render_machine import render_utils  # noqa: E402
from render_machine import terminal_process  # noqa: E402
from render_machine._conpty import ConPtyProcess  # noqa: E402
from render_machine._legacy_pipe import LegacyPipeProcess  # noqa: E402
from render_machine.terminal_process import (  # noqa: E402
    ENVIRONMENT_ERROR_EXIT_CODE,
    NO_INPUT_NOTE,
    NO_PTY_ENV_VAR,
    InputDisposition,
    TerminalEnvironmentError,
    create_terminal_process,
)

# Generous relative to the operations they cover, so a failure means a hang rather than a
# slow machine.
SPAWN_TIMEOUT = 30.0
WAIT_TIMEOUT = 30.0
TEARDOWN_BOUND = 25.0
POLL = 0.05

SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WAIT_OBJECT_0 = 0

# The backend's own binding, used only to assert its declarations and to observe the calls
# it makes. Everything this module calls for its own purposes goes through a second binding,
# so a test never adds a declaration production code then depends on.
kernel32 = _conpty.kernel32

probe = ctypes.WinDLL("kernel32", use_last_error=True)
probe.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
probe.OpenProcess.restype = wintypes.HANDLE
probe.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
probe.WaitForSingleObject.restype = wintypes.DWORD
probe.CloseHandle.argtypes = [wintypes.HANDLE]
probe.CloseHandle.restype = wintypes.BOOL


def write_program(tmp_path: Path, name: str, source: str) -> str:
    """Writes a probe program, and refuses to write one that will not parse.

    A target that dies on a parse error reports a bare non-zero exit code and whatever the
    terminal happened to catch, which is the least useful evidence available. Compiling here,
    while the text is still in hand, fails the test at the write with the source and the line.
    """
    path = tmp_path / f"{name}.py"
    program = textwrap.dedent(source)
    compile(program, str(path), "exec")
    path.write_text(program, encoding="utf-8")
    return str(path)


def command(script: str, *args: str):
    return [sys.executable, "-I", script, *args]


def wait_for(predicate, timeout=WAIT_TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(POLL)
    return bool(predicate())


def wait_for_output(process, needle, timeout=WAIT_TIMEOUT):
    if wait_for(lambda: needle in process.normalized_output(), timeout):
        return True
    raise AssertionError(f"{needle!r} never reached the transcript, which held {process.normalized_output()!r}")


def wait_for_exit(process, timeout=WAIT_TIMEOUT):
    assert wait_for(lambda: process.poll() is not None, timeout), "the script never exited"
    return process.poll()


def process_is_gone(pid: int, timeout=WAIT_TIMEOUT) -> bool:
    handle = probe.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return True  # already reaped, so there is nothing left to wait for
    try:
        return probe.WaitForSingleObject(handle, int(timeout * 1000)) == WAIT_OBJECT_0
    finally:
        probe.CloseHandle(handle)


def live_backend_threads():
    return [thread for thread in threading.enumerate() if thread.name.startswith("codeplain-conpty-")]


class _HandleLedger:
    """Every handle the backend opened through kernel32, minus the ones it closed again."""

    def __init__(self):
        self.open = {}

    def opened(self, handle, description):
        if handle:
            self.open[int(handle)] = description

    def closed(self, handle):
        if handle:
            self.open.pop(int(handle), None)

    def outstanding(self):
        return dict(self.open)


@pytest.fixture
def handle_ledger(monkeypatch):
    """A ledger of the backend's own handles rather than GetProcessHandleCount().

    A process-wide count is not a leak detector here: CPython allocates a kernel semaphore
    per lock object, so the count moves for reasons that have nothing to do with this
    backend, and waiting for it to settle waits on the garbage collector.
    """
    ledger = _HandleLedger()
    originals = {
        name: getattr(kernel32, name)
        for name in (
            "CreatePipe",
            "CreateJobObjectW",
            "OpenThread",
            "CreateProcessW",
            "CreatePseudoConsole",
            "CloseHandle",
            "ClosePseudoConsole",
        )
    }

    def create_pipe(read_slot, write_slot, attributes, size):
        ok = originals["CreatePipe"](read_slot, write_slot, attributes, size)
        if ok:
            ledger.opened(read_slot._obj.value, "pipe read end")
            ledger.opened(write_slot._obj.value, "pipe write end")
        return ok

    def create_job(attributes, name):
        handle = originals["CreateJobObjectW"](attributes, name)
        ledger.opened(handle, "job object")
        return handle

    def open_thread(access, inherit, thread_id):
        handle = originals["OpenThread"](access, inherit, thread_id)
        ledger.opened(handle, "thread handle")
        return handle

    def create_process(*arguments):
        ok = originals["CreateProcessW"](*arguments)
        if ok:
            information = arguments[-1]._obj
            ledger.opened(information.hProcess, "process handle")
            ledger.opened(information.hThread, "thread handle of the process")
        return ok

    def create_pseudoconsole(size, input_handle, output_handle, flags, slot):
        hresult = originals["CreatePseudoConsole"](size, input_handle, output_handle, flags, slot)
        if hresult == 0:
            ledger.opened(slot._obj.value, "pseudoconsole")
        return hresult

    def close_handle(handle):
        ledger.closed(handle if isinstance(handle, int) else handle.value)
        return originals["CloseHandle"](handle)

    def close_pseudoconsole(handle):
        ledger.closed(handle if isinstance(handle, int) else handle.value)
        originals["ClosePseudoConsole"](handle)

    for name, replacement in (
        ("CreatePipe", create_pipe),
        ("CreateJobObjectW", create_job),
        ("OpenThread", open_thread),
        ("CreateProcessW", create_process),
        ("CreatePseudoConsole", create_pseudoconsole),
        ("CloseHandle", close_handle),
        ("ClosePseudoConsole", close_pseudoconsole),
    ):
        monkeypatch.setattr(kernel32, name, replacement)
    return ledger


@pytest.fixture(autouse=True)
def no_backend_threads_outlive_the_test():
    """Fails the test that leaked a pump rather than the one that runs after it.

    A reader or writer left running owns handles and keeps appending to a transcript nobody
    reads, and every later assertion about threads or handles then measures the leak instead
    of its own subject.
    """
    yield
    assert wait_for(
        lambda: not live_backend_threads(), timeout=20.0
    ), f"backend threads outlived the test: {[thread.name for thread in live_backend_threads()]}"


@pytest.fixture
def backend():
    process = ConPtyProcess()
    try:
        yield process
    finally:
        try:
            process.terminate_tree(grace=0.1)
        except TerminalEnvironmentError:
            pass
        try:
            process.close()
        except TerminalEnvironmentError:
            pass


# The probe reports what a script sees, one short line at a time: the pseudoconsole wraps at
# the configured width, so a single long line would come back folded.
# Written at column zero and without a single escape sequence: the target parses this file
# on its own, and the two ways a program embedded in a test can arrive malformed — an indent
# no longer shared by every line, and an escape the test source resolves too early — are both
# absent by construction rather than by review.
TERMINAL_PROBE = """
import ctypes
import os
import traceback


def report():
    # Declared: an undeclared call returns c_int, which truncates a handle and reports a
    # false negative for both questions below.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetStdHandle.argtypes = [ctypes.c_uint]
    kernel32.GetStdHandle.restype = ctypes.c_void_p
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.GetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
    kernel32.GetConsoleMode.restype = ctypes.c_int
    kernel32.IsProcessInJob.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    kernel32.IsProcessInJob.restype = ctypes.c_int

    mode = ctypes.c_uint(0)
    console = kernel32.GetConsoleMode(kernel32.GetStdHandle(0xFFFFFFF5), ctypes.byref(mode))
    in_job = ctypes.c_int(0)
    kernel32.IsProcessInJob(kernel32.GetCurrentProcess(), None, ctypes.byref(in_job))
    return [
        "ISATTY=%s" % (os.isatty(0) and os.isatty(1) and os.isatty(2)),
        "CONSOLE=%s" % bool(console),
        "INJOB=%s" % bool(in_job.value),
        "TERM=%s" % os.environ.get("TERM"),
        "DONE",
    ]


try:
    lines = report()
except BaseException:
    lines = ["PROBE-FAILED"] + traceback.format_exc().splitlines()

# Written beside the probe as well as printed: the file survives a target whose standard
# handles are not the terminal's, and it needs no argument that could itself go wrong.
beside_the_probe = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe.txt")
with open(beside_the_probe, "w", encoding="utf-8") as handle:
    for line in lines:
        print(line, file=handle)

for line in lines:
    print(line)
"""


# ------------------------------------------------------------------ declarations


def test_every_handle_returning_call_is_declared_pointer_wide():
    """ctypes converts a return value as c_int unless told otherwise, which truncates a
    64-bit handle long before any ownership rule can help."""
    pointer_width = ctypes.sizeof(ctypes.c_void_p)

    for name in ("CreateJobObjectW", "OpenThread", "GetProcessHeap"):
        assert ctypes.sizeof(getattr(kernel32, name).restype) == pointer_width, name
    assert ctypes.sizeof(kernel32.HeapAlloc.restype) == pointer_width
    assert ctypes.sizeof(_conpty.HANDLE) == pointer_width


def test_the_void_calls_are_declared_as_returning_nothing():
    assert kernel32.ClosePseudoConsole.restype is None
    assert kernel32.DeleteProcThreadAttributeList.restype is None


def test_the_pseudoconsole_calls_are_declared_as_signed_32_bit_results():
    """HRESULT is the inverse of the BOOL convention every other call here uses."""
    assert ctypes.sizeof(kernel32.CreatePseudoConsole.restype) == 4
    assert kernel32.CreatePseudoConsole.restype(-1).value == -1


def test_an_allocated_pointer_survives_the_declared_return_type():
    heap = kernel32.GetProcessHeap()
    buffer = kernel32.HeapAlloc(heap, 0, 4096)
    try:
        assert buffer is not None and buffer > 0
        # A truncating declaration turns a pointer with high bits set into a negative int.
        assert buffer == ctypes.c_void_p(buffer).value
    finally:
        assert kernel32.HeapFree(heap, 0, buffer)


def test_a_failing_bool_call_reports_the_captured_last_error():
    ok = probe.CloseHandle(wintypes.HANDLE(0))
    error = ctypes.get_last_error()

    assert not ok
    assert str(error) in str(_conpty._win_error("Closing a handle", error))


def test_a_failing_pseudoconsole_call_is_reported_as_its_hresult():
    """A zero-sized console is rejected outright, where invalid handles are not: Windows
    Server 2022 accepts handles it will only fail on later. The failure has to be detected as
    a nonzero HRESULT rather than read from the last error, which these calls do not promise
    to set."""
    session = SimpleNamespace(hPC=_conpty.HPCON(), hPC_valid=False)

    with pytest.raises(TerminalEnvironmentError) as error:
        _conpty._create_pseudoconsole(session, 0, 0, -1, -1)

    assert "HRESULT" in str(error.value)
    assert session.hPC_valid is False  # a failed HRESULT output is never closable


def test_a_build_without_pseudoconsole_support_is_an_environment_error(monkeypatch):
    monkeypatch.setattr(_conpty, "PSEUDOCONSOLE_AVAILABLE", False)

    with pytest.raises(TerminalEnvironmentError) as error:
        _conpty._require_pseudoconsole_support()

    assert str(_conpty.MIN_CONPTY_BUILD) in str(error.value)
    assert "fallback" in str(error.value)


# --------------------------------------------------------------- backend selection


def test_windows_selects_the_conpty_backend(monkeypatch):
    monkeypatch.delenv(NO_PTY_ENV_VAR, raising=False)

    process = create_terminal_process()
    try:
        assert isinstance(process, ConPtyProcess)
    finally:
        process.close()


def test_the_backend_notes_that_it_has_no_synthetic_end_of_file():
    """The timeout diagnostic asks the backend that ran, so this one has to state the
    asymmetry itself: a script reading input blocks instead of seeing end-of-file."""
    note = ConPtyProcess().no_input_note()

    assert note.startswith(NO_INPUT_NOTE)
    assert "end-of-file" in note


def test_the_teardown_budget_covers_every_phase_the_shutdown_spends():
    """The CLI derives its own wait from this, and the ConPTY pipeline is the longest one."""
    assert _conpty.TEARDOWN_BUDGET_SECONDS > terminal_process.SIGTERM_GRACE_PERIOD_SECONDS
    assert terminal_process.teardown_budget_seconds() >= _conpty.TEARDOWN_BUDGET_SECONDS


def test_the_escape_hatch_still_selects_the_pipe_backend_on_windows(monkeypatch):
    """The hatch is cross-platform: it is read before the platform branch, not instead of it."""
    monkeypatch.setenv(NO_PTY_ENV_VAR, "1")

    process = create_terminal_process()
    try:
        assert isinstance(process, LegacyPipeProcess)
    finally:
        process.close()


# ------------------------------------------------------------------- the lifecycle


def test_a_script_runs_on_a_real_console_inside_the_job(backend, tmp_path):
    script = write_program(tmp_path, "terminal_probe", TERMINAL_PROBE)
    report_path = tmp_path / "probe.txt"

    backend.spawn(command(script))
    exit_code = wait_for_exit(backend)
    backend.terminate_tree(grace=0.1)
    backend.close()
    output = backend.normalized_output()
    report = report_path.read_text(encoding="utf-8") if report_path.exists() else "(no report was written)"
    written = "".join(Path(script).read_text(encoding="utf-8").splitlines(keepends=True)[:5])
    evidence = (
        f"transcript={output!r}\nthe target reported:\n{report}\n"
        f"first lines written={written!r}\nliteral={TERMINAL_PROBE[:80]!r}"
    )

    assert exit_code == 0, evidence
    assert "ISATTY=True" in output, evidence
    assert "CONSOLE=True" in output, evidence
    assert "INJOB=True" in output, evidence
    assert "TERM=xterm-256color" in output, evidence


def test_the_exit_code_is_reported_verbatim(backend, tmp_path):
    script = write_program(tmp_path, "exit_seven", "import sys\nsys.exit(7)\n")

    backend.spawn(command(script))

    assert wait_for_exit(backend) == 7


def test_input_written_through_the_stored_session_reaches_the_script(backend, tmp_path):
    """The one field whose absence only shows up when everything else went right."""
    script = write_program(
        tmp_path,
        "echo_line",
        """
        import sys

        print("READY", flush=True)
        line = sys.stdin.readline().strip()
        print("GOT[%s]" % line, flush=True)
        """,
    )

    backend.spawn(command(script))
    assert wait_for_output(backend, "READY")
    result = backend.write_input(b"hello\r")

    assert result.disposition is InputDisposition.ACCEPTED
    assert wait_for_output(backend, "GOT[hello]")
    assert wait_for_exit(backend) == 0


def test_the_script_is_a_member_of_the_sessions_job(backend, tmp_path):
    """Membership comes from the attribute list at creation, so there is no window in which
    the process exists outside the job."""
    script = write_program(tmp_path, "waits", "print('READY', flush=True)\nimport time\ntime.sleep(120)\n")

    backend.spawn(command(script))
    assert wait_for_output(backend, "READY")
    session = backend._owner.session
    member = wintypes.BOOL(0)

    assert kernel32.IsProcessInJob(session.proc.process_handle(), session.hJob, ctypes.byref(member))
    assert member.value


def test_a_descendant_is_terminated_with_the_script(backend, tmp_path):
    script = write_program(
        tmp_path,
        "spawns_a_child",
        f"""
        import subprocess
        import sys
        import time

        child = subprocess.Popen([r"{sys.executable}", "-c", "import time; time.sleep(120)"])
        print("CHILD=%d" % child.pid, flush=True)
        time.sleep(120)
        """,
    )

    backend.spawn(command(script))
    assert wait_for_output(backend, "CHILD=")
    line = [part for part in backend.normalized_output().split() if part.startswith("CHILD=")][0]
    descendant = int(line.split("=", 1)[1])

    started = time.monotonic()
    backend.terminate_tree(grace=0.2)
    backend.close()

    assert time.monotonic() - started < TEARDOWN_BOUND
    assert process_is_gone(descendant)


def test_teardown_completes_within_its_bound_for_a_script_that_ignores_everything(backend, tmp_path):
    """The failure mode this guards is a hang, not an exception: `ClosePseudoConsole()`
    blocks on pre-24H2 builds unless the output pipe is drained or closed."""
    script = write_program(
        tmp_path,
        "ignores_signals",
        """
        import signal
        import sys
        import time

        signal.signal(signal.SIGINT, signal.SIG_IGN)
        print("READY", flush=True)
        while True:
            print("noise" * 200, flush=True)
            time.sleep(0.01)
        """,
    )

    backend.spawn(command(script))
    assert wait_for_output(backend, "READY")

    started = time.monotonic()
    backend.terminate_tree(grace=0.5)
    backend.close()

    assert time.monotonic() - started < TEARDOWN_BOUND


def test_the_graceful_signal_reaches_a_registered_handler_before_the_grace_expires(backend, tmp_path):
    script = write_program(
        tmp_path,
        "handles_ctrl_c",
        """
        import signal
        import sys
        import time


        def handler(signum, frame):
            print("HANDLED", flush=True)
            sys.exit(42)


        signal.signal(signal.SIGINT, handler)
        print("READY", flush=True)
        time.sleep(120)
        """,
    )

    backend.spawn(command(script))
    assert wait_for_output(backend, "READY")

    backend.terminate_tree(grace=10.0)

    assert wait_for_output(backend, "HANDLED", timeout=5.0)
    assert backend.poll() == 42  # its own exit status, not the job's termination code


def test_the_renderers_own_console_is_untouched_by_the_graceful_signal(backend, tmp_path):
    """The Windows analogue of signalling our own process group, and the one catastrophic
    failure: the control byte goes into the pseudoconsole, never through
    `GenerateConsoleCtrlEvent`."""
    script = write_program(tmp_path, "waits", "import time\ntime.sleep(120)\n")
    interrupted = threading.Event()
    previous = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, lambda *_: interrupted.set())
    try:
        backend.spawn(command(script))
        backend.terminate_tree(grace=0.5)
        backend.close()
    finally:
        signal.signal(signal.SIGINT, previous)

    assert not interrupted.is_set()


# --------------------------------------------------------------- fault injection


def failing(name):
    def raiser(*args, **kwargs):
        raise TerminalEnvironmentError(f"{name} failed by injection")

    return raiser


def failing_after(monkeypatch, name):
    """Fails once the named step has really run, so the rollback faces a real resource.

    Injecting before the call proves only that nothing was allocated; these cases are the
    ones that prove the allocation is released.
    """
    original = getattr(_conpty, name)

    def wrapper(*args, **kwargs):
        result = original(*args, **kwargs)
        raise TerminalEnvironmentError(f"{name} failed by injection after the real call")

    monkeypatch.setattr(_conpty, name, wrapper)


def failing_on_call(monkeypatch, name, call_index):
    """Fails one specific call of a step that runs more than once."""
    original = getattr(_conpty, name)
    calls = {"count": 0}

    def wrapper(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == call_index:
            raise TerminalEnvironmentError(f"{name} call {call_index} failed by injection")
        return original(*args, **kwargs)

    monkeypatch.setattr(_conpty, name, wrapper)


@pytest.mark.parametrize(
    "step",
    [
        "_create_job",
        "_set_kill_on_job_close",
        "_create_pseudoconsole",
        "_initialize_attribute_list",
        "_create_process",
        "_open_thread_handle",
    ],
)
def test_a_failed_step_leaves_no_process_thread_or_handle_behind(monkeypatch, handle_ledger, tmp_path, step):
    script = write_program(tmp_path, "never_runs", "print('unreachable')\n")
    monkeypatch.setattr(_conpty, step, failing(step))
    process = ConPtyProcess()

    with pytest.raises(TerminalEnvironmentError):
        process.spawn(command(script))
    process.close()

    assert "unreachable" not in process.normalized_output()
    assert wait_for(lambda: not live_backend_threads(), timeout=10.0)
    assert wait_for(lambda: not handle_ledger.outstanding(), timeout=10.0), handle_ledger.outstanding()


@pytest.mark.parametrize(
    "step",
    ["_create_job", "_create_pseudoconsole", "_initialize_attribute_list"],
)
def test_a_failure_after_a_real_native_step_releases_what_that_step_allocated(
    monkeypatch, handle_ledger, tmp_path, step
):
    script = write_program(tmp_path, "never_runs", "print('unreachable')\n")
    failing_after(monkeypatch, step)
    process = ConPtyProcess()

    with pytest.raises(TerminalEnvironmentError):
        process.spawn(command(script))
    process.close()

    assert wait_for(lambda: not live_backend_threads(), timeout=10.0)
    assert wait_for(lambda: not handle_ledger.outstanding(), timeout=10.0), handle_ledger.outstanding()


def test_a_failure_after_create_process_leaves_no_surviving_child(monkeypatch, handle_ledger, tmp_path):
    """The widest rollback: the child already exists, and the job it was created inside is
    what takes it down."""
    script = write_program(tmp_path, "waits", "import time\ntime.sleep(120)\n")
    original = _conpty._create_process
    created = []

    def create_then_fail(command_line, directory, environment, attrs, proc):
        original(command_line, directory, environment, attrs, proc)
        created.append(int(proc.pi.dwProcessId))
        raise TerminalEnvironmentError("injected after the child was created")

    monkeypatch.setattr(_conpty, "_create_process", create_then_fail)
    process = ConPtyProcess()

    with pytest.raises(TerminalEnvironmentError):
        process.spawn(command(script))
    process.close()

    assert created, "the injection never ran the real call"
    assert process_is_gone(created[0])
    assert wait_for(lambda: not live_backend_threads(), timeout=10.0)
    assert wait_for(lambda: not handle_ledger.outstanding(), timeout=10.0), handle_ledger.outstanding()


@pytest.mark.parametrize("call_index", [1, 2])
def test_a_failed_pipe_leaves_nothing_behind(monkeypatch, tmp_path, call_index):
    """Both `CreatePipe` calls are separate failure sites; the second is the one a coarser
    cleanup scope mishandles while the first still looks correct."""
    script = write_program(tmp_path, "never_runs", "print('unreachable')\n")
    failing_on_call(monkeypatch, "_create_pipe", call_index)
    process = ConPtyProcess()

    with pytest.raises(TerminalEnvironmentError):
        process.spawn(command(script))
    process.close()

    assert wait_for(lambda: not live_backend_threads(), timeout=10.0)


@pytest.mark.parametrize("call_index", [1, 2])
def test_a_failed_attribute_update_leaves_nothing_behind(monkeypatch, tmp_path, call_index):
    script = write_program(tmp_path, "never_runs", "print('unreachable')\n")
    failing_on_call(monkeypatch, "_update_attribute", call_index)
    process = ConPtyProcess()

    with pytest.raises(TerminalEnvironmentError):
        process.spawn(command(script))
    process.close()

    assert wait_for(lambda: not live_backend_threads(), timeout=10.0)


def test_a_reader_that_cannot_start_fails_before_there_is_anything_to_roll_back(monkeypatch, tmp_path):
    script = write_program(tmp_path, "never_runs", "print('unreachable')\n")
    original = threading.Thread.start

    def refuse(self):
        if self.name == "codeplain-conpty-reader":
            raise RuntimeError("can't start new thread")
        original(self)

    monkeypatch.setattr(threading.Thread, "start", refuse)
    process = ConPtyProcess()

    with pytest.raises(RuntimeError):
        process.spawn(command(script))
    process.close()

    assert not live_backend_threads()


def test_a_reader_that_dies_while_the_process_is_being_created_still_unwinds(monkeypatch, tmp_path):
    """The widest window in the sequence: process creation is its slowest step."""
    script = write_program(tmp_path, "waits", "import time\ntime.sleep(120)\n")
    process = ConPtyProcess()
    original = _conpty._create_process

    def create_then_fail_the_reader(*args, **kwargs):
        original(*args, **kwargs)
        process.reader_exc = OSError("the reader died during creation")
        process.reader_failed.set()

    monkeypatch.setattr(_conpty, "_create_process", create_then_fail_the_reader)

    with pytest.raises(TerminalEnvironmentError):
        process.spawn(command(script))
    process.close()

    assert wait_for(lambda: not live_backend_threads(), timeout=10.0)


def test_a_zero_return_from_create_process_keeps_its_garbage_fields_unclosed(monkeypatch, tmp_path):
    """`CreateProcessW` writes nothing meaningful on failure, so the fields it leaves behind
    must never be treated as handles."""
    script = write_program(tmp_path, "never_runs", "print('unreachable')\n")
    sentinel = 0x0BADF00D
    closed = []
    original_close = kernel32.CloseHandle

    def record(handle):
        closed.append(handle)
        return original_close(handle)

    def fail_with_sentinels(command_line, directory, environment, attrs, proc):
        proc.pi.hProcess = sentinel
        proc.pi.hThread = sentinel
        raise _conpty._win_error("Starting the script", 2)

    monkeypatch.setattr(kernel32, "CloseHandle", record)
    monkeypatch.setattr(_conpty, "_create_process", fail_with_sentinels)
    process = ConPtyProcess()

    with pytest.raises(TerminalEnvironmentError):
        process.spawn(command(script))
    process.close()

    assert closed, "the rollback closed nothing, so the absence below would prove nothing"
    assert sentinel not in closed  # the `proc.valid` gate keeps a failed call's fields unclosed
    assert wait_for(lambda: not live_backend_threads(), timeout=10.0)


def test_a_teardown_that_outlives_its_bound_is_handed_to_the_finalizer(monkeypatch, handle_ledger, backend, tmp_path):
    """The foreground returns promptly and reports the failure on the environment channel;
    the finalizer, not the foreground, closes the pseudoconsole and releases the session."""
    script = write_program(tmp_path, "waits", "import time\ntime.sleep(120)\n")
    closed_on = []
    original_close = kernel32.ClosePseudoConsole
    original_wait = _conpty._SessionBundle._await_job_empty
    waits = {"count": 0}

    def record(handle):
        closed_on.append(threading.current_thread().name)
        original_close(handle)

    def expire_once(self, bound):
        """Expires the foreground's wait, then lets the finalizer's own attempt succeed."""
        waits["count"] += 1
        return False if waits["count"] == 1 else original_wait(self, bound)

    monkeypatch.setattr(kernel32, "ClosePseudoConsole", record)
    monkeypatch.setattr(_conpty._SessionBundle, "_await_job_empty", expire_once)
    monkeypatch.setattr(_conpty, "FINALIZER_TICK_SECONDS", 0.05)

    backend.spawn(command(script))
    child = int(backend._owner.session.proc.pi.dwProcessId)
    started = time.monotonic()
    with pytest.raises(TerminalEnvironmentError) as error:
        backend.close()

    assert time.monotonic() - started < TEARDOWN_BOUND
    assert "finalizer" in str(error.value)
    assert threading.current_thread().name not in closed_on
    # Ownership was transferred, not dropped: the session is released on the finalizer's own
    # time, the child goes with it, and every handle comes back.
    assert wait_for(lambda: closed_on == ["codeplain-conpty-finalizer"], timeout=30.0)
    assert wait_for(
        lambda: not any(thread.name == "codeplain-conpty-finalizer" for thread in threading.enumerate()),
        timeout=30.0,
    )
    assert process_is_gone(child)
    assert wait_for(lambda: not handle_ledger.outstanding(), timeout=30.0), handle_ledger.outstanding()


def native_call_log(monkeypatch):
    """One ordered log of the two native calls whose relative order is load-bearing."""
    events = []
    original_close_handle = kernel32.CloseHandle
    original_close_pty = kernel32.ClosePseudoConsole

    def close_handle(handle):
        events.append(("CloseHandle", handle, threading.current_thread().name))
        return original_close_handle(handle)

    def close_pseudoconsole(handle):
        events.append(("ClosePseudoConsole", handle, threading.current_thread().name))
        original_close_pty(handle)

    monkeypatch.setattr(kernel32, "CloseHandle", close_handle)
    monkeypatch.setattr(kernel32, "ClosePseudoConsole", close_pseudoconsole)
    return events


def index_of(events, name, handle=None):
    for position, (called, argument, _thread) in enumerate(events):
        if called == name and (handle is None or argument == handle):
            return position
    return None


def test_a_finalizer_that_runs_out_of_time_still_closes_the_job_first(monkeypatch, backend, tmp_path):
    """The abandoned path is the one most likely to face a live process tree, and
    `ClosePseudoConsole()` can block on this build — so kill-on-job-close must not be queued
    behind it."""
    script = write_program(tmp_path, "waits", "import time\ntime.sleep(120)\n")
    events = native_call_log(monkeypatch)
    monkeypatch.setattr(_conpty._SessionBundle, "_await_job_empty", lambda self, bound: False)
    monkeypatch.setattr(_conpty, "FINALIZER_DEADLINE_SECONDS", 0.2)
    monkeypatch.setattr(_conpty, "FINALIZER_TICK_SECONDS", 0.05)

    backend.spawn(command(script))
    session = backend._owner.session
    child = int(session.proc.pi.dwProcessId)
    job = session.hJob
    with pytest.raises(TerminalEnvironmentError):
        backend.close()

    assert wait_for(
        lambda: not any(thread.name == "codeplain-conpty-finalizer" for thread in threading.enumerate()),
        timeout=30.0,
    )
    job_closed = index_of(events, "CloseHandle", job)
    pseudoconsole_closed = index_of(events, "ClosePseudoConsole")
    assert job_closed is not None, "the abandoned session never released its job"
    assert pseudoconsole_closed is not None
    assert job_closed < pseudoconsole_closed
    assert process_is_gone(child)  # kill-on-job-close, which is what closing the job buys


def test_a_finalizer_that_cannot_start_leaves_the_session_owned_and_released(monkeypatch, backend, tmp_path):
    """Nothing took the session, so the foreground keeps it and releases the natives itself
    rather than dropping the only reference to a live job."""
    script = write_program(tmp_path, "waits", "import time\ntime.sleep(120)\n")
    events = native_call_log(monkeypatch)
    monkeypatch.setattr(_conpty._SessionBundle, "_await_job_empty", lambda self, bound: False)
    original_start = threading.Thread.start

    def refuse(self):
        if self.name == "codeplain-conpty-finalizer":
            raise RuntimeError("can't start new thread")
        original_start(self)

    monkeypatch.setattr(threading.Thread, "start", refuse)

    backend.spawn(command(script))
    session = backend._owner.session
    child = int(session.proc.pi.dwProcessId)
    job = session.hJob
    started = time.monotonic()
    with pytest.raises(TerminalEnvironmentError) as error:
        backend.close()

    assert time.monotonic() - started < TEARDOWN_BOUND
    assert "no finalizer thread could be started" in str(error.value)
    assert backend._owner is not None  # ownership retained rather than dropped
    assert index_of(events, "CloseHandle", job) is not None
    assert process_is_gone(child)
    # Released rather than leaked: the writer was idle on its queue, and the last-resort
    # release stops it through the sentinel before it decides about the input handles.
    assert not session.in_w.owned and not session.writer_handle.owned


# ------------------------------------------------------------------- marshaling


@pytest.mark.parametrize(
    "kwargs",
    [
        {"command": [sys.executable, "-c", "print('x')\x00"]},
        {"cwd": "C:\\builds\x00"},
        {"env": {"NAME\x00": "value"}},
        {"env": {"NAME": "value\x00"}},
        {"env": {"NA=ME": "value"}},
        {"env": {"": "value"}},
    ],
)
def test_an_input_windows_cannot_carry_is_refused_before_any_process_is_created(kwargs, tmp_path):
    marker = tmp_path / "ran.txt"
    argv = kwargs.pop("command", [sys.executable, "-c", f"open(r'{marker}', 'w').close()"])
    if "env" in kwargs:
        kwargs["env"] = dict(os.environ, **kwargs["env"])
    process = ConPtyProcess()

    with pytest.raises(TerminalEnvironmentError):
        process.spawn(argv, **kwargs)
    process.close()

    assert not marker.exists()  # asserted by observation, not only by the exception


# ---------------------------------------------------------------- cancellation


def test_a_stop_event_set_before_the_spawn_launches_nothing(tmp_path):
    marker = tmp_path / "ran.txt"
    script = write_program(tmp_path, "marks", f"open(r'{marker}', 'w').close()\n")
    stop = threading.Event()
    stop.set()
    process = ConPtyProcess()

    with pytest.raises(RenderCancelledError):
        process.spawn(command(script), stop_event=stop)
    process.close()

    assert not wait_for(marker.exists, timeout=2.0)


def test_a_cancellation_observed_while_the_session_is_built_never_launches_the_target(monkeypatch, tmp_path):
    """The window between the first check and `CreateProcessW` is the whole session setup;
    a render cancelled inside it must not run the script's side effects."""
    marker = tmp_path / "ran.txt"
    script = write_program(tmp_path, "marks", f"open(r'{marker}', 'w').close()\n")
    stop = threading.Event()
    original = _conpty._create_pseudoconsole

    def create_then_cancel(*args, **kwargs):
        original(*args, **kwargs)
        stop.set()

    monkeypatch.setattr(_conpty, "_create_pseudoconsole", create_then_cancel)
    process = ConPtyProcess()

    with pytest.raises(RenderCancelledError):
        process.spawn(command(script), stop_event=stop)
    process.close()

    assert not wait_for(marker.exists, timeout=2.0)
    assert wait_for(lambda: not live_backend_threads(), timeout=10.0)


# ------------------------------------------------------- the blocked input channel


SILENT_READER = """
    import time

    print("READY", flush=True)
    time.sleep(120)
"""


def writer_progress(backend):
    """What the writer has consumed: the item under its cursor and the bytes still owed."""
    queue = backend._writer.queue
    current = queue.current()
    position = None if current is None else (current.sequence, current.cursor)
    return position, queue.pending_bytes()


def test_a_saturated_input_channel_still_tears_down_within_the_bound(backend, tmp_path):
    """The target never reads, so the writer is expected to end up parked inside a synchronous
    `WriteFile` — the state the cancel loop and the bounded join exist for.

    Whether it truly parks is not this side's decision: the pseudoconsole drains the pipe into
    its own input buffer, so on some builds every item lands and the writer stays idle. The
    bounded-progress sample below distinguishes the two worlds, and each is asserted for what
    it can prove — the parked one that the cancel path ran at all, both of them that teardown
    stays inside its bound and the tree dies.
    """
    script = write_program(tmp_path, "silent_reader", SILENT_READER)
    backend.spawn(command(script))
    assert wait_for_output(backend, "READY")
    child = int(backend._owner.session.proc.pi.dwProcessId)

    accepted = 0
    for _ in range(64):
        result = backend.write_input(b"x" * 4096 + b"\r")
        if result.disposition is not InputDisposition.ACCEPTED:
            break
        accepted += 1

    first = writer_progress(backend)
    time.sleep(0.5)
    second = writer_progress(backend)
    parked = first == second and first[0] is not None and first[1] > 0

    started = time.monotonic()
    backend.terminate_tree(grace=0.5)
    backend.close()

    assert accepted > 0
    if parked:
        # A writer that never moved could only be released by the cancel loop.
        assert backend._writer.cancels > 0
    assert time.monotonic() - started < TEARDOWN_BOUND
    assert process_is_gone(child)


PROCESSED_INPUT_OFF = """
    import ctypes
    import time

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.GetStdHandle(-10)
    mode = ctypes.c_uint(0)
    kernel32.GetConsoleMode(handle, ctypes.byref(mode))
    kernel32.SetConsoleMode(handle, mode.value & ~0x0001)  # ENABLE_PROCESSED_INPUT

    print("READY", flush=True)
    time.sleep(120)
"""


def test_a_client_that_cleared_processed_input_falls_through_to_forced_termination(backend, tmp_path):
    """The best-effort boundary, proven rather than asserted: without ENABLE_PROCESSED_INPUT
    the control byte is just a byte, so the grace expires and the job takes the tree."""
    script = write_program(tmp_path, "no_processed_input", PROCESSED_INPUT_OFF)
    backend.spawn(command(script))
    assert wait_for_output(backend, "READY")
    child = int(backend._owner.session.proc.pi.dwProcessId)

    started = time.monotonic()
    backend.terminate_tree(grace=1.0)
    backend.close()

    assert time.monotonic() - started < TEARDOWN_BOUND
    assert process_is_gone(child)
    # Never exited on its own, so no status was ever read: the contrast with the handled
    # case, which reports the code its handler chose.
    assert backend.poll() is None


TERMINAL_QUERY = """
    import msvcrt
    import sys
    import time

    sys.stdout.write("\\x1b[6n")  # device status report: a terminal is expected to answer
    sys.stdout.flush()

    reply = ""
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not reply.endswith("R"):
        if msvcrt.kbhit():
            reply += msvcrt.getwch()
        else:
            time.sleep(0.01)

    print("ANSWERED=%s" % reply.endswith("R"), flush=True)
    print("QUERIED", flush=True)
"""


def test_a_target_that_queries_the_terminal_receives_its_reply(backend, tmp_path):
    """The target reads its own console input back, so this proves delivery rather than the
    absence of a recorded failure. Which side answers — the pseudoconsole's own emulator or
    the renderer's responder — is not asserted; that a querying target is not left waiting is.
    """
    script = write_program(tmp_path, "queries", TERMINAL_QUERY)

    backend.spawn(command(script))
    exit_code = wait_for_exit(backend)
    backend.terminate_tree(grace=0.1)
    backend.close()
    output = backend.normalized_output()

    assert exit_code == 0
    assert "QUERIED" in output
    assert "ANSWERED=True" in output
    assert backend.terminal_reply_failed is False, backend.terminal_reply_detail()


# ----------------------------------------------------- through execute_script


SCRIPT_TYPE = "Unit"


def write_powershell(tmp_path: Path, name: str, body: str) -> str:
    path = tmp_path / f"{name}.ps1"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(path)


def run_script(script: str, timeout: int, stop_event=None):
    """One execution through the real renderer path, artifacts cleaned up afterwards."""
    exit_code, output, artifact = render_utils.execute_script(
        script, [], SCRIPT_TYPE, timeout=timeout, stop_event=stop_event
    )
    if artifact is not None:
        for path in (artifact, artifact + render_utils.RAW_OUTPUT_SUFFIX):
            try:
                os.unlink(path)
            except OSError:
                pass
    return exit_code, output


REPORTING_SCRIPT = """
    Write-Output "HELLO-CONPTY"
    exit 0
"""

FAILING_SCRIPT = """
    Write-Output "BEFORE-EXIT"
    exit 3
"""

SINGLE_READ_SCRIPT = """
    Write-Output "READY"
    $line = [Console]::In.ReadLine()
    Write-Output "GOT $line"
"""

REPEATED_READ_SCRIPT = """
    Write-Output "READY"
    while ($true) {
        $line = [Console]::In.ReadLine()
        Write-Output "GOT $line"
    }
"""


def test_a_powershell_script_runs_through_execute_script_and_reports_its_output(tmp_path):
    script = write_powershell(tmp_path, "reports", REPORTING_SCRIPT)

    exit_code, output = run_script(script, timeout=90)

    assert exit_code == 0
    assert "HELLO-CONPTY" in output


def test_a_failing_powershell_script_returns_its_exit_code_verbatim(tmp_path):
    script = write_powershell(tmp_path, "fails", FAILING_SCRIPT)

    exit_code, output = run_script(script, timeout=90)

    assert exit_code == 3
    assert "BEFORE-EXIT" in output


@pytest.mark.parametrize(
    "name,body",
    [("reads_once", SINGLE_READ_SCRIPT), ("reads_repeatedly", REPEATED_READ_SCRIPT)],
)
def test_a_script_that_reads_input_runs_to_the_timeout_and_says_why(tmp_path, name, body):
    """The documented Windows asymmetry: ConPTY carries no synthetic end-of-file, so a read
    blocks until the timeout instead of returning EOF the way it does on POSIX."""
    script = write_powershell(tmp_path, name, body)

    exit_code, output = run_script(script, timeout=15)

    assert exit_code == render_utils.TIMEOUT_ERROR_EXIT_CODE
    assert "no input driver was attached" in output.lower()
    assert "end-of-file" in output.lower()


def test_a_cancelled_script_raises_instead_of_publishing_an_outcome(tmp_path):
    """Cancellation while the script is blocked on a read it will never satisfy: the run
    raises rather than waiting out the timeout it would otherwise reach."""
    marker = tmp_path / "started.txt"
    script = write_powershell(
        tmp_path,
        "cancelled_read",
        f"""
        New-Item -ItemType File -Path "{marker}" | Out-Null
        Write-Output "READY"
        $line = [Console]::In.ReadLine()
        Write-Output "GOT $line"
        """,
    )
    stop = threading.Event()
    watcher = threading.Thread(target=lambda: stop.set() if wait_for(marker.exists, timeout=90.0) else None)
    watcher.daemon = True
    watcher.start()

    started = time.monotonic()
    with pytest.raises(RenderCancelledError):
        run_script(script, timeout=180, stop_event=stop)

    assert time.monotonic() - started < 90.0  # cancelled, not timed out
    watcher.join(5.0)


@pytest.mark.parametrize(
    "symbol,result",
    [
        ("WaitForSingleObject", 0xFFFFFFFF),  # WAIT_FAILED
        ("GetExitCodeProcess", 0),
        ("QueryInformationJobObject", 0),
        ("TerminateJobObject", 0),
    ],
)
def test_a_native_call_failing_after_launch_is_an_environment_error(monkeypatch, tmp_path, symbol, result):
    """The post-launch seams: a wait, an exit-code read, a job query or a job termination that
    fails describes a run whose outcome nobody could observe, so it takes the 69 channel rather
    than being reported as a timeout or a clean pass."""
    script = write_powershell(tmp_path, "reports", REPORTING_SCRIPT)
    monkeypatch.setattr(kernel32, symbol, lambda *arguments: result)

    exit_code, output = run_script(script, timeout=90)

    assert exit_code == ENVIRONMENT_ERROR_EXIT_CODE
    assert "could not be executed" in output
