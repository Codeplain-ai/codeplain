"""Windows ConPTY backend for `TerminalProcess`.

One pseudoconsole backs the target's standard handles, and one Job Object contains the
process tree it starts. Both are attached at creation: the job goes into the same
proc-thread attribute list as the pseudoconsole, so the child is either created inside the
job or not created at all, and `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` makes the kernel tear
the tree down when the last job handle closes.

A single reader thread owns the output pipe's read handle for its whole lifetime, and a
single writer thread owns every write to the input pipe. The writer exists because the
input pipe is anonymous and therefore synchronous: a target that stops reading leaves
`WriteFile` blocked until it is cancelled, and only the writer's own thread handle is
registered for that cancellation.

Ownership is incremental from before the first allocation. Every handle is wrapped in a
holder carrying an owned flag, the rollback stack always registers `close_if_owned`, and
handing a resource on is `take()` — flag first, value second, never the reverse.

Two platform asymmetries are deliberate and documented here rather than hidden:

* The job is a stronger containment than a POSIX process group. It survives `setpgid`-style
  escapes and the kernel enforces it, whereas PTY hangup delivers a signal a target may
  ignore.
* There is no synthetic end-of-file. POSIX injects `VEOF` when no input driver is attached;
  ConPTY has no parent-side equivalent that keeps the input channel open, and the channel
  has to stay open for the graceful control byte and for terminal-query replies. A script
  that reads input therefore blocks until the execution timeout rather than seeing EOF.
"""

import codecs
import ctypes
import sys
import threading
import time
from contextlib import ExitStack
from ctypes import wintypes
from typing import Callable, List, Optional, Sequence, Tuple

from plain2code_console import console
from plain2code_exceptions import RenderCancelledError
from render_machine._conpty_support import (
    CANCEL_TICK_SECONDS,
    WRITER_JOIN_DEADLINE_SECONDS,
    GateDecision,
    InputQueue,
    InputWriter,
    WriteAborted,
    WriteChannel,
    build_command_line,
    build_environment_block,
    native_thread_id,
    reply_resolution,
    validate_working_directory,
)
from render_machine.output_normalizer import OutputNormalizer
from render_machine.terminal_process import (
    CONTROL_DELIVERY_DEADLINE_SECONDS,
    DEFAULT_TERM,
    DRAIN_DEADLINE_SECONDS,
    GRACE_TICK_SECONDS,
    HANDSHAKE_TIMEOUT_SECONDS,
    POLL_INTERVAL_SECONDS,
    READ_CHUNK_BYTES,
    REAP_DEADLINE_SECONDS,
    SIGTERM_GRACE_PERIOD_SECONDS,
    TERMINAL_COLUMNS,
    TERMINAL_ROWS,
    InputWriteResult,
    TerminalEnvironmentError,
    TerminalProcess,
    child_environment,
)
from render_machine.terminal_queries import TerminalQueryResponder

if sys.platform != "win32":  # pragma: no cover - the ConPTY backend is Windows-only
    raise ImportError("render_machine._conpty is Windows-only")

# ------------------------------------------------------------------ FFI: types
#
# Declared before any lifecycle code. ctypes converts return values as c_int by default,
# which truncates 64-bit handles, heap pointers and attribute-list addresses before any
# ownership rule can help, so every imported function below carries explicit argtypes and
# restype: pointer-width types for HANDLE / HPCON / PVOID / SIZE_T, BOOL for the Win32 BOOL
# APIs, signed 32-bit for the HRESULT-returning pseudoconsole calls, and None for the void
# ones.

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

HANDLE = wintypes.HANDLE
PHANDLE = ctypes.POINTER(HANDLE)
HPCON = wintypes.HANDLE
PHPCON = ctypes.POINTER(HPCON)
DWORD = wintypes.DWORD
LPDWORD = ctypes.POINTER(DWORD)
BOOL = wintypes.BOOL
PBOOL = ctypes.POINTER(BOOL)
LPVOID = ctypes.c_void_p
SIZE_T = ctypes.c_size_t
PSIZE_T = ctypes.POINTER(SIZE_T)
ULONG_PTR = ctypes.c_size_t
LARGE_INTEGER = wintypes.LARGE_INTEGER

S_OK = 0

EXTENDED_STARTUPINFO_PRESENT = 0x00080000
STARTF_USESTDHANDLES = 0x00000100
CREATE_UNICODE_ENVIRONMENT = 0x00000400
PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
PROC_THREAD_ATTRIBUTE_COUNT = 2

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOBOBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS = 1
JOBOBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9

THREAD_TERMINATE = 0x0001

STD_INPUT_HANDLE = 0xFFFFFFF6
STD_OUTPUT_HANDLE = 0xFFFFFFF5
STD_ERROR_HANDLE = 0xFFFFFFF4
INVALID_HANDLE_VALUE = 0xFFFFFFFFFFFFFFFF

ERROR_HANDLE_EOF = 38
ERROR_BROKEN_PIPE = 109
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_OPERATION_ABORTED = 995
ERROR_NOT_FOUND = 1168

WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258  # the only wait result that means "still running"

# ConPTY ships from Windows 10 1809. Below it there is no fallback: a silent downgrade to
# pipes would make execution behaviour depend on the machine again.
MIN_CONPTY_BUILD = 17763

# The forced-termination exit code the job reports for its members.
JOB_TERMINATION_EXIT_CODE = 1

# How long the finalizer keeps retrying a teardown the foreground had to abandon.
FINALIZER_DEADLINE_SECONDS = 60.0
FINALIZER_TICK_SECONDS = 0.5

_OWNER_PARENT = "parent"
_OWNER_READER = "reader"

# The graceful signal: writing 0x03 into the pseudoconsole input is how terminal emulators
# deliver Ctrl-C to a ConPTY client. `GenerateConsoleCtrlEvent` cannot be used, because it
# reaches only processes sharing the caller's console and the target is on the pseudoconsole.
CONTROL_C_BYTE = b"\x03"


class COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", DWORD),
        ("dwY", DWORD),
        ("dwXSize", DWORD),
        ("dwYSize", DWORD),
        ("dwXCountChars", DWORD),
        ("dwYCountChars", DWORD),
        ("dwFillAttribute", DWORD),
        ("dwFlags", DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", HANDLE),
        ("hStdOutput", HANDLE),
        ("hStdError", HANDLE),
    ]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", LPVOID)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", HANDLE), ("hThread", HANDLE), ("dwProcessId", DWORD), ("dwThreadId", DWORD)]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", LARGE_INTEGER),
        ("PerJobUserTimeLimit", LARGE_INTEGER),
        ("LimitFlags", DWORD),
        ("MinimumWorkingSetSize", SIZE_T),
        ("MaximumWorkingSetSize", SIZE_T),
        ("ActiveProcessLimit", DWORD),
        ("Affinity", ULONG_PTR),
        ("PriorityClass", DWORD),
        ("SchedulingClass", DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", SIZE_T),
        ("JobMemoryLimit", SIZE_T),
        ("PeakProcessMemoryUsed", SIZE_T),
        ("PeakJobMemoryUsed", SIZE_T),
    ]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", LARGE_INTEGER),
        ("TotalKernelTime", LARGE_INTEGER),
        ("ThisPeriodTotalUserTime", LARGE_INTEGER),
        ("ThisPeriodTotalKernelTime", LARGE_INTEGER),
        ("TotalPageFaultCount", DWORD),
        ("TotalProcesses", DWORD),
        ("ActiveProcesses", DWORD),
        ("TotalTerminatedProcesses", DWORD),
    ]


# -------------------------------------------------------------- FFI: functions


def _declare(name: str, argtypes: Sequence[object], restype: Optional[object]):
    function = getattr(kernel32, name)
    function.argtypes = list(argtypes)
    function.restype = restype
    return function


_declare("CloseHandle", [HANDLE], BOOL)
_declare("CreatePipe", [PHANDLE, PHANDLE, LPVOID, DWORD], BOOL)
_declare("ReadFile", [HANDLE, LPVOID, DWORD, LPDWORD, LPVOID], BOOL)
_declare("WriteFile", [HANDLE, LPVOID, DWORD, LPDWORD, LPVOID], BOOL)
_declare("GetProcessHeap", [], HANDLE)
_declare("HeapAlloc", [HANDLE, DWORD, SIZE_T], LPVOID)
_declare("HeapFree", [HANDLE, DWORD, LPVOID], BOOL)
_declare("InitializeProcThreadAttributeList", [LPVOID, DWORD, DWORD, PSIZE_T], BOOL)
_declare("UpdateProcThreadAttribute", [LPVOID, DWORD, ULONG_PTR, LPVOID, SIZE_T, LPVOID, PSIZE_T], BOOL)
_declare("DeleteProcThreadAttributeList", [LPVOID], None)
_declare(
    "CreateProcessW",
    [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        LPVOID,
        LPVOID,
        BOOL,
        DWORD,
        LPVOID,
        wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOEXW),
        ctypes.POINTER(PROCESS_INFORMATION),
    ],
    BOOL,
)
_declare("CreateJobObjectW", [LPVOID, wintypes.LPCWSTR], HANDLE)
_declare("SetInformationJobObject", [HANDLE, ctypes.c_int, LPVOID, DWORD], BOOL)
_declare("QueryInformationJobObject", [HANDLE, ctypes.c_int, LPVOID, DWORD, LPDWORD], BOOL)
_declare("TerminateJobObject", [HANDLE, wintypes.UINT], BOOL)
_declare("IsProcessInJob", [HANDLE, HANDLE, PBOOL], BOOL)
_declare("GetExitCodeProcess", [HANDLE, LPDWORD], BOOL)
_declare("WaitForSingleObject", [HANDLE, DWORD], DWORD)
_declare("OpenThread", [DWORD, BOOL, DWORD], HANDLE)
_declare("GetStdHandle", [DWORD], HANDLE)
_declare("GetConsoleMode", [HANDLE, LPDWORD], BOOL)
_declare("CancelSynchronousIo", [HANDLE], BOOL)


def _declare_pseudoconsole_api() -> bool:
    """Binds the three ConPTY entry points, or reports that this build has none.

    Their restype is signed 32-bit rather than `ctypes.HRESULT`, which would raise an
    `OSError` of its own: the failure has to be reported as the HRESULT itself, because
    these calls do not promise to set the last error.
    """
    if not hasattr(kernel32, "CreatePseudoConsole"):
        return False
    _declare("CreatePseudoConsole", [COORD, HANDLE, HANDLE, DWORD, PHPCON], ctypes.c_long)
    _declare("ResizePseudoConsole", [HPCON, COORD], ctypes.c_long)
    _declare("ClosePseudoConsole", [HPCON], None)
    return True


PSEUDOCONSOLE_AVAILABLE = _declare_pseudoconsole_api()


# ------------------------------------------------------------------- FFI: errors


def _win_error(action: str, error: int) -> TerminalEnvironmentError:
    return TerminalEnvironmentError(f"{action} failed: Windows error {error} ({ctypes.FormatError(error)}).")


def _hresult_error(action: str, hresult: int) -> TerminalEnvironmentError:
    return TerminalEnvironmentError(f"{action} failed: HRESULT 0x{hresult & 0xFFFFFFFF:08X}.")


def _windows_build() -> int:
    return int(sys.getwindowsversion().build)


def _require_pseudoconsole_support() -> None:
    build = _windows_build()
    if PSEUDOCONSOLE_AVAILABLE and build >= MIN_CONPTY_BUILD:
        return
    raise TerminalEnvironmentError(
        f"This Windows build ({build}) has no pseudoconsole support, so a script cannot be given a "
        f"terminal. Codeplain needs Windows 10 build {MIN_CONPTY_BUILD} (1809) or newer. There is no "
        "pipe fallback, because execution behaviour must not depend on the machine."
    )


def _close_handle(handle: Optional[int]) -> None:
    if not handle:
        return
    kernel32.CloseHandle(handle)


# ------------------------------------------------------------------- ownership


class _PipePair:
    """Validity shared by both endpoints of one pipe.

    A failed `CreatePipe` leaves whatever was in the two slots behind, so neither endpoint
    may be closed on that path. One flag, consulted by both closers, is what keeps them from
    disagreeing.
    """

    def __init__(self) -> None:
        self.valid = False


class _Holder:
    """One handle plus the flag that says whether this side still owns it.

    `take()` flips the flag and then returns the value. Closing first and disarming
    afterwards leaves a window in which rollback holds a handle Windows may already have
    recycled — the corruption this helper exists to prevent.
    """

    def __init__(self, pair: Optional[_PipePair] = None) -> None:
        self.value = HANDLE()
        self.owned = True
        self._pair = pair
        self._lock = threading.Lock()

    @property
    def slot(self):
        """The address every API writes straight into, so there is no copy-out step."""
        return ctypes.byref(self.value)

    def handle(self) -> Optional[int]:
        if not self.owned or (self._pair is not None and not self._pair.valid):
            return None
        return self.value.value

    def take(self) -> Optional[int]:
        with self._lock:
            if not self.owned or (self._pair is not None and not self._pair.valid):
                return None
            self.owned = False
            return self.value.value

    def close_if_owned(self) -> None:
        _close_handle(self.take())


class _AttrList:
    """The attribute list's two ownership states.

    `InitializeProcThreadAttributeList()` does not allocate, so the buffer and the
    initialized list are separate states with separate cleanups: a buffer alone is freed,
    while an initialized list is deleted first and only then freed.
    """

    def __init__(self) -> None:
        self.buffer: Optional[int] = None
        self.initialized = False
        self.owned = True

    def dispose(self) -> None:
        buffer, self.buffer = self.buffer, None
        initialized, self.initialized = self.initialized, False
        self.owned = False
        if buffer is None:
            return
        if initialized:
            kernel32.DeleteProcThreadAttributeList(buffer)
        kernel32.HeapFree(kernel32.GetProcessHeap(), 0, buffer)

    def dispose_if_owned(self) -> None:
        if self.owned:
            self.dispose()


class _ProcInfo:
    """`PROCESS_INFORMATION`: two handles the kernel fills into one pre-owned struct.

    Both are owned from the moment the call returns. Recording only the process handle and
    leaving the thread handle for later means an unwind at the next check leaks it.
    """

    def __init__(self) -> None:
        self.pi = PROCESS_INFORMATION()
        self.valid = False
        self._lock = threading.Lock()

    def _take(self, name: str) -> Optional[int]:
        with self._lock:
            if not self.valid:  # a failed call leaves garbage in both fields
                return None
            handle = getattr(self.pi, name)
            setattr(self.pi, name, None)
            return handle

    def take_process(self) -> Optional[int]:
        return self._take("hProcess")

    def take_thread(self) -> Optional[int]:
        return self._take("hThread")

    def process_handle(self) -> Optional[int]:
        return self.pi.hProcess if self.valid else None

    def close_all(self) -> None:
        _close_handle(self.take_process())
        _close_handle(self.take_thread())


class _ReaderHandles:
    """The output read handle, whose ownership moves to the reader in one assignment.

    `owner` is the single field that decides, so rollback and reader can never disagree and
    there is no state in which the handle has left one owner without reaching the other.
    """

    def __init__(self, pair: _PipePair) -> None:
        self.owner = _OWNER_PARENT
        self.out_r = HANDLE()
        self._pair = pair
        self._lock = threading.Lock()

    @property
    def slot(self):
        return ctypes.byref(self.out_r)

    def take(self) -> Optional[int]:
        with self._lock:
            if not self._pair.valid:
                return None
            handle = self.out_r.value
            self.out_r = HANDLE()
            return handle

    def close_if_owner_is_parent(self) -> None:
        if self.owner == _OWNER_PARENT:
            _close_handle(self.take())


# -------------------------------------------------------------- native helpers
#
# Every native step of the spawn sequence goes through one of these, so a test can fail a
# single step and assert what the rollback releases.


def _create_pipe(read_holder, write_holder, pair: _PipePair) -> None:
    ok = kernel32.CreatePipe(read_holder.slot, write_holder.slot, None, 0)
    if not ok:
        error = ctypes.get_last_error()  # captured before formatting or any other call
        raise _win_error("Creating a terminal pipe", error)
    pair.valid = True


def _create_job(session: "_SessionBundle") -> None:
    """Stores the job in the session before returning.

    Returning the handle for the caller to assign leaves a window in which nothing owns it:
    an interruption between the two loses the job, and with it the containment its whole
    purpose is.
    """
    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        error = ctypes.get_last_error()
        raise _win_error("Creating the job object for the script's process tree", error)
    session.hJob = handle


def _set_kill_on_job_close(job: int) -> None:
    """The crash-safety backstop: the kernel tears the tree down when the last handle closes."""
    limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = kernel32.SetInformationJobObject(
        job,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
        ctypes.byref(limits),
        ctypes.sizeof(limits),
    )
    if not ok:
        error = ctypes.get_last_error()
        raise _win_error("Configuring the job object", error)


def _create_pseudoconsole(session, columns: int, rows: int, in_r: int, out_w: int) -> None:
    """Writes into the session's slot and arms it, both inside this call.

    Arming from the caller would leave a pseudoconsole nobody may close if anything came
    between the two statements.
    """
    size = COORD(ctypes.c_short(columns), ctypes.c_short(rows))
    hresult = kernel32.CreatePseudoConsole(size, in_r, out_w, 0, ctypes.byref(session.hPC))
    if hresult != S_OK:  # HRESULT, not BOOL: success is zero and failure is everything else
        raise _hresult_error("Creating the pseudoconsole", hresult)
    session.hPC_valid = True  # armed only after S_OK


def _initialize_attribute_list(attrs: _AttrList, count: int) -> None:
    """The three-call protocol: size, allocate, initialize.

    The sizing call fails by design, but only one failure is the expected one — anything
    else is a real error that must abort rather than flow into a zero-byte allocation.
    """
    size = SIZE_T(0)
    ok = kernel32.InitializeProcThreadAttributeList(None, count, 0, ctypes.byref(size))
    error = ctypes.get_last_error()
    if ok:
        raise TerminalEnvironmentError("Sizing the process attribute list unexpectedly succeeded.")
    if error != ERROR_INSUFFICIENT_BUFFER:
        raise _win_error("Sizing the process attribute list", error)
    if size.value == 0:
        raise TerminalEnvironmentError("Sizing the process attribute list reported a zero-byte list.")
    buffer = kernel32.HeapAlloc(kernel32.GetProcessHeap(), 0, size.value)
    if not buffer:  # HeapAlloc reports failure by returning NULL rather than raising
        raise TerminalEnvironmentError(f"Allocating {size.value} bytes for the process attribute list failed.")
    attrs.buffer = buffer  # state one: free only
    ok = kernel32.InitializeProcThreadAttributeList(buffer, count, 0, ctypes.byref(size))
    if not ok:
        error = ctypes.get_last_error()
        raise _win_error("Initializing the process attribute list", error)
    attrs.initialized = True  # state two: delete, then free


def _update_attribute(attrs: _AttrList, attribute: int, value, size: int, description: str) -> None:
    ok = kernel32.UpdateProcThreadAttribute(attrs.buffer, 0, attribute, value, size, None, None)
    if not ok:
        error = ctypes.get_last_error()
        raise _win_error(f"Adding the {description} to the process attribute list", error)


def _create_process(
    command_line: str,
    directory: Optional[str],
    environment: str,
    attrs: _AttrList,
    proc: _ProcInfo,
) -> None:
    startup = STARTUPINFOEXW()
    startup.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
    startup.lpAttributeList = attrs.buffer
    if _renderer_output_is_redirected():
        # Declared, and left NULL. Without this, CreateProcessW hands the child a copy of the
        # renderer's own standard handles: verified on Windows Server 2022, where the target
        # wrote into the renderer's redirected stdout and read end-of-file from its stdin
        # while still attached to the pseudoconsole and to its job. Declaring the handles and
        # supplying none stops that copy, and the console the child is attached to supplies
        # its standard handles instead. A renderer that owns a console is left on the path
        # that already reaches the pseudoconsole, so this never changes the interactive case.
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES
    # CreateProcessW may modify lpCommandLine in place, so it is handed a writable buffer.
    command_buffer = ctypes.create_unicode_buffer(command_line)
    # The buffer's own terminator supplies the block's second NUL.
    environment_buffer = ctypes.create_unicode_buffer(environment)
    ok = kernel32.CreateProcessW(
        None,
        ctypes.cast(command_buffer, wintypes.LPWSTR),
        None,
        None,
        False,
        EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT,
        ctypes.cast(environment_buffer, LPVOID),
        directory,
        ctypes.byref(startup),
        ctypes.byref(proc.pi),
    )
    if not ok:  # zero is failure, and ctypes does not raise on it
        error = ctypes.get_last_error()
        raise _win_error("Starting the script", error)
    proc.valid = True  # closers ignore the garbage a failed call leaves behind


def _renderer_output_is_redirected() -> bool:
    """True when any of the renderer's own standard handles is not a console.

    It decides whether the child needs protecting from them. When the renderer sits on a
    console, `CreateProcessW` swaps the child's standard handles for its own console's and
    the pseudoconsole is reached as intended. When the renderer is redirected — every CI run,
    every piped invocation — the same call copies those files or pipes into the child, which
    then writes past the pseudoconsole entirely and reads end-of-file instead of input.
    """
    for identifier in (STD_INPUT_HANDLE, STD_OUTPUT_HANDLE, STD_ERROR_HANDLE):
        handle = kernel32.GetStdHandle(identifier)
        if not handle or handle == INVALID_HANDLE_VALUE:
            return True
        mode = DWORD(0)
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return True
    return False


def _open_thread_handle(holder: "_Holder", native_id: int) -> None:
    """THREAD_TERMINATE is what `CancelSynchronousIo()` requires.

    The handle is opened while the thread it names is certainly alive — the writer parked on
    its gate, the reader before its first read — because an id is recyclable the instant its
    thread exits. It lands in the holder inside this call, so no interruption can lose it.
    """
    handle = kernel32.OpenThread(THREAD_TERMINATE, False, native_id)
    if not handle:
        error = ctypes.get_last_error()
        raise _win_error("Opening a handle to a terminal pump thread", error)
    holder.value = HANDLE(handle)


def _job_active_processes(job: int) -> int:
    """Members still running. Raises rather than reporting an empty job it never observed."""
    info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
    returned = DWORD(0)
    ok = kernel32.QueryInformationJobObject(
        job,
        JOBOBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS,
        ctypes.byref(info),
        ctypes.sizeof(info),
        ctypes.byref(returned),
    )
    if not ok:
        error = ctypes.get_last_error()
        raise _win_error("Querying the job object for its remaining members", error)
    return int(info.ActiveProcesses)


# ------------------------------------------------------------------ the session


class _PseudoconsoleInput(WriteChannel):
    """`WriteFile` on the input pipe, cancelled through the writer's own thread handle."""

    def __init__(self, session: "_SessionBundle") -> None:
        self._session = session

    def write(self, data: bytes) -> int:
        handle = self._session.in_w.handle()
        if not handle:
            raise BrokenPipeError("the terminal input channel is closed")
        written = DWORD(0)
        ok = kernel32.WriteFile(handle, data, len(data), ctypes.byref(written), None)
        if not ok:
            error = ctypes.get_last_error()
            if error == ERROR_OPERATION_ABORTED:
                raise WriteAborted("the terminal input write was cancelled")
            raise _win_error("Writing to the script's terminal input", error)
        return int(written.value)

    def cancel(self) -> None:
        handle = self._session.writer_handle.handle()
        if not handle:
            return
        ok = kernel32.CancelSynchronousIo(handle)
        if not ok:
            error = ctypes.get_last_error()
            if error != ERROR_NOT_FOUND:  # nothing was in flight; the next tick tries again
                console.debug(f"cancelling the terminal input write reported Windows error {error}")


class _SessionBundle:
    """Everything that lives as long as the session, plus the one ordered teardown.

    The teardown is invoked explicitly rather than registered as a stack callback: it is the
    single step that can time out and hand its resources away, and a callback cannot do that
    safely while `ExitStack.close()` is mid-unwind on the same stack.
    """

    def __init__(self, out_w: _Holder, in_pair: _PipePair, in_queue: InputQueue) -> None:
        self.out_w = out_w
        self.in_w = _Holder(pair=in_pair)
        self.in_queue = in_queue
        self.writer: Optional[InputWriter] = None
        self.writer_handle = _Holder()  # the same take-then-close discipline as every handle
        self.reader: Optional[threading.Thread] = None
        self.reader_handle = _Holder()  # opened by the reader itself, for cancelling its read
        self.hPC = HPCON()
        self.hPC_valid = False  # a failed HRESULT output is never closable
        self.hJob: Optional[int] = None
        self.job_array = (HANDLE * 1)()  # must outlive the attribute list that points at it
        self.proc = _ProcInfo()
        self.exit_code: Optional[int] = None
        # The first native failure any of the observation or teardown steps hit. A failed
        # wait, exit-code read, job query or job termination cannot be recovered from and
        # must not be read as "still running" or "shut down cleanly", so it is published
        # here and surfaces on the environment channel.
        self.failure: Optional[str] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------- observation

    def record_failure(self, detail: str) -> None:
        """Keeps the first failure: later ones are usually consequences of it."""
        if self.failure is None:
            self.failure = detail
        console.debug(f"terminal session failure: {detail}")

    def poll_exit_code(self) -> Optional[int]:
        """Non-blocking exit status. `WaitForSingleObject` decides, so a target that exits
        with 259 is not mistaken for one that is still running.

        Only `WAIT_TIMEOUT` means "still running". Every other non-signalled result, and a
        failed exit-code read, is an infrastructure failure: reporting it as a running
        process would turn it into a 124 timeout instead of a 69 environment error.
        """
        failure = None
        with self._lock:
            if self.exit_code is not None:
                return self.exit_code
            handle = self.proc.process_handle()
            if not handle:
                return None
            waited = kernel32.WaitForSingleObject(handle, 0)
            if waited == WAIT_TIMEOUT:
                return None
            if waited != WAIT_OBJECT_0:
                error = ctypes.get_last_error()
                failure = f"waiting on the script's process reported result 0x{waited:08X} (Windows error {error})"
            else:
                code = DWORD(0)
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    self.exit_code = int(code.value)
                    return self.exit_code
                error = ctypes.get_last_error()
                failure = f"reading the script's exit code failed: Windows error {error}"
        self.record_failure(failure)  # outside the lock: recording logs
        return None

    def running(self) -> bool:
        """False once the process handle has been released, whatever the target is doing:
        nothing after that point may wait on it."""
        return self.proc.process_handle() is not None and self.poll_exit_code() is None

    # ---------------------------------------------------------------- teardown

    def teardown(self, grace: Optional[float]) -> bool:
        """The ordered shutdown. True when it ran out of bound and must be handed off.

        Idempotent: every step takes what it releases, so a repeated call finds nothing left
        to do. `grace` of None skips the graceful phase, which is what every forced path and
        every rollback wants.
        """
        self.poll_exit_code()  # the only place a status is read; never after a forced kill
        if grace is not None and self.running():
            self._graceful_phase(grace)
        self._terminate_job()
        if not self._await_job_empty(REAP_DEADLINE_SECONDS):
            return True
        if not self._stop_writer():
            # The writer is still inside a write on `in_w`, which teardown is about to
            # close. Closing a handle underneath a blocked write is what the hand-off exists
            # to avoid.
            return True
        # The job handle closes before the pseudoconsole, never after: ClosePseudoConsole()
        # can block on builds before 24H2, and kill-on-job-close must not be waiting behind
        # a call that might not return.
        self._close_job()
        self._close_pseudoconsole()
        self._release_handles()
        return False

    def _graceful_phase(self, grace: float) -> None:
        """Delivery and grace are two different bounds: queue delay must not consume the
        target's cleanup time."""
        writer = self.writer
        if writer is None:
            return
        if not writer.deliver_control(CONTROL_C_BYTE, CONTROL_DELIVERY_DEADLINE_SECONDS):
            return  # undelivered: escalate now rather than waiting out a grace nobody received
        deadline = time.monotonic() + grace  # a fresh monotonic interval, started on delivery
        while time.monotonic() < deadline:
            if not self.running():
                return
            time.sleep(GRACE_TICK_SECONDS)

    def _terminate_job(self) -> None:
        job = self.hJob
        if job is None:
            return
        if not kernel32.TerminateJobObject(job, JOB_TERMINATION_EXIT_CODE):
            error = ctypes.get_last_error()
            # The forced step of the shutdown: if it did not run, nothing else in this
            # teardown can claim the tree is gone.
            self.record_failure(f"terminating the script's job object failed: Windows error {error}")

    def _await_job_empty(self, bound: float) -> bool:
        """Closes the process handles, then waits for the job's membership to reach zero."""
        self.proc.close_all()
        job = self.hJob
        if job is None:
            return True
        deadline = time.monotonic() + bound
        while True:
            try:
                active = _job_active_processes(job)
            except TerminalEnvironmentError as exc:
                # Unanswerable rather than empty. Teardown continues — closing the job handle
                # is still the kill-on-close backstop — but the run is an environment failure.
                self.record_failure(str(exc))
                return True
            if active == 0:
                return True
            if time.monotonic() >= deadline:
                console.debug(f"the job object still held {active} processes after {bound}s")
                return False
            time.sleep(POLL_INTERVAL_SECONDS)

    def _stop_writer(self) -> bool:
        writer = self.writer
        if writer is None:
            return True
        return writer.stop(WRITER_JOIN_DEADLINE_SECONDS)

    def adopt_reader_thread(self) -> None:
        """The reader opens a handle to itself, so a parked read can be cancelled later.

        Opened by the thread rather than derived from a recorded id at cancel time: a thread
        id is recyclable the instant its thread exits, and this is the one moment the thread
        is certainly alive.
        """
        try:
            _open_thread_handle(self.reader_handle, native_thread_id())
        except BaseException as exc:  # cancellation degrades to the bounded join alone
            console.debug(f"the terminal output reader could not open a handle to itself: {exc!r}")

    def join_reader(self, bound: float) -> bool:
        """Joins the reader, cancelling its read if the output pipe never reached end-of-file.

        `ClosePseudoConsole()` does not always break a parked `ReadFile`: verified on Windows
        Server 2022, where a session that never had a client left the reader waiting on a pipe
        whose write end was gone. Cancellation is retried like the writer's, because a cancel
        issued between two reads reaches nothing. True when the reader has finished.
        """
        reader = self.reader
        if reader is None or reader.ident is None:  # None when it never started
            return True
        reader.join(timeout=bound)
        deadline = time.monotonic() + bound
        while reader.is_alive():
            self._cancel_reader()
            reader.join(timeout=CANCEL_TICK_SECONDS)
            if time.monotonic() >= deadline:
                break
        if reader.is_alive():
            return False
        self.reader_handle.close_if_owned()  # nothing can be cancelled through it any more
        return True

    def _cancel_reader(self) -> None:
        handle = self.reader_handle.handle()
        if not handle:
            return
        if not kernel32.CancelSynchronousIo(handle):
            error = ctypes.get_last_error()
            if error != ERROR_NOT_FOUND:  # nothing was in flight; the next tick tries again
                console.debug(f"cancelling the terminal output read reported Windows error {error}")

    def _close_pseudoconsole(self) -> None:
        """The precondition is the output pipe: drained *or* closed, never neither.

        One sequence serves both branches, which is why there is no test on the reader here:
        the close happens while a live reader is still draining, and a reader that has
        already failed closed the read handle before it published anything, so the same call
        finds the pipe closed. The join only follows the close, never precedes it, and this
        never runs on the reader thread. What the close does not guarantee is that the read
        itself ends, which is why the join cancels it.
        """
        if not self.hPC_valid:
            return
        self.hPC_valid = False
        _close_handle(self.out_w.take())  # the write side must go, or the reader never sees EOF
        kernel32.ClosePseudoConsole(self.hPC)
        self.hPC = HPCON()
        self.join_reader(DRAIN_DEADLINE_SECONDS)

    def _release_handles(self) -> None:
        self.in_w.close_if_owned()  # after the writer has stopped, never before
        self.writer_handle.close_if_owned()
        if not self.reader_alive():  # kept while a parked read may still need cancelling
            self.reader_handle.close_if_owned()
        self._close_job()
        self.proc.close_all()

    def _close_job(self) -> None:
        job, self.hJob = self.hJob, None  # taken before it is closed, like every other handle
        _close_handle(job)  # closing it is also the kill-on-close backstop

    def reader_alive(self) -> bool:
        reader = self.reader
        return reader is not None and reader.ident is not None and reader.is_alive()

    def release_abandoned(self) -> None:
        """Last-resort release for a teardown nobody can finish.

        Reached only when the finalizer runs out of time or could not be started, which is
        also when the process tree is most likely still alive — so the job goes first, and
        `ClosePseudoConsole()` only after it. Reversed, a call that can block on builds before
        24H2 would stand between a failing teardown and the kill-on-job-close that is the
        whole backstop.

        The writer is stopped here as well as in the ordered teardown, because this path is
        reached when the teardown gave up before it got that far: an idle writer is parked on
        its queue rather than inside a write, and the stop sentinel is the only thing that
        ever releases it. `inputWriteSide` and the writer's thread handle are leaked only when
        the stop itself runs out of bound — closing a handle underneath a blocked `WriteFile`
        is the corruption the bounded teardown exists to avoid.
        """
        self._terminate_job()
        self.proc.close_all()
        self._close_job()  # kill-on-job-close fires before anything that can block
        self._close_pseudoconsole()
        if self._stop_writer():
            self.in_w.close_if_owned()
            self.writer_handle.close_if_owned()
        else:
            console.debug("leaking the terminal input handles: the writer never returned")


class _SessionOwner:
    """The session and its rollback stack, as one reference.

    `armed` is the commit flag: while it is set the stack alone owns everything and the
    session is not yet a session. Storing the owner early is harmless for exactly that
    reason, and the commit is the single flip.
    """

    def __init__(self, session: _SessionBundle, stack: ExitStack) -> None:
        self.session = session
        self.stack = stack
        self.armed = True


def _unreleased_session_error(context: str, handed_off: bool) -> TerminalEnvironmentError:
    """One message for both hand-off sites, saying which of the two things happened."""
    if handed_off:
        return TerminalEnvironmentError(
            f"The script's terminal session {context} and was handed to the background finalizer."
        )
    return TerminalEnvironmentError(
        f"The script's terminal session {context}, and no finalizer thread could be started for it: "
        "it was released as far as it safely could be."
    )


def _hand_off_to_finalizer(owner: _SessionOwner) -> bool:
    """Transfers the owner to a daemon finalizer. False when no thread could be started.

    The caller keeps the owner on a false result: dropping the only reference to a session
    nobody has taken is how a job, a pseudoconsole and two handles survive until Codeplain
    exits.
    """
    thread = threading.Thread(target=_finalize_session, args=(owner,), name="codeplain-conpty-finalizer", daemon=True)
    try:
        thread.start()
    except BaseException as exc:  # thread exhaustion is the realistic one
        console.debug(f"the terminal session finalizer could not be started: {exc!r}")
        return False
    return True


def _finalize_session(owner: _SessionOwner) -> None:
    """Finishes a teardown that outlived the foreground's bound, then closes the stack.

    The stack is closed only once the session's own release has run, so there is exactly one
    owner at every instant and the transfer never races an unwind in progress.
    """
    deadline = time.monotonic() + FINALIZER_DEADLINE_SECONDS
    try:
        while True:
            if not owner.session.teardown(None):
                break
            if time.monotonic() >= deadline:
                console.debug("the terminal session finalizer gave up on an unfinished teardown")
                break
            time.sleep(FINALIZER_TICK_SECONDS)
    except BaseException as exc:  # nothing here can be reported anywhere useful
        console.debug(f"the terminal session finalizer failed: {exc!r}")
    finally:
        try:
            # The stack holds startup and pipe state only, so giving up on the teardown
            # without this leaves the job, the pseudoconsole and the input handle behind —
            # and with the job handle open, kill-on-job-close never fires.
            owner.session.release_abandoned()
        except BaseException as exc:
            console.debug(f"the terminal session finalizer could not release the session: {exc!r}")
        try:
            owner.stack.close()
        except BaseException as exc:
            console.debug(f"the terminal session finalizer could not release its handles: {exc!r}")


# ------------------------------------------------------------------- the backend


class ConPtyProcess(TerminalProcess):
    """One command, one pseudoconsole, one job, one reader thread, one writer thread."""

    def __init__(self) -> None:
        self.reader_failed = threading.Event()
        self.reader_exc: Optional[BaseException] = None

        self._spawned = False
        self._closed = False
        self._stop_event = threading.Event()
        self._input_driver: Optional[object] = None
        self._owner: Optional[_SessionOwner] = None
        self._writer: Optional[InputWriter] = None
        self._input_queue = InputQueue()

        self._output_lock = threading.Lock()
        self._decoded: List[str] = []
        self._raw = bytearray()

        # The parser runs live in the reader, because terminals answer queries: a
        # render-afterwards parser would leave a querying target hanging.
        self.query_responder = TerminalQueryResponder(self._admit_reply)
        self.normalizer = OutputNormalizer(reply_handler=self.query_responder.answer)
        self._byte_sink: Callable[[bytes], None] = self.normalizer.feed

    # ---------------------------------------------------------------- public API

    def spawn(
        self,
        command: Sequence[str],
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        terminal_size: Tuple[int, int] = (TERMINAL_COLUMNS, TERMINAL_ROWS),
        stop_event: Optional[threading.Event] = None,
        input_driver: Optional[object] = None,
        spawn_timeout: float = HANDSHAKE_TIMEOUT_SECONDS,
    ) -> None:
        if self._spawned:
            raise RuntimeError("ConPtyProcess instances are single-use")
        self._spawned = True
        self._stop_event = stop_event if stop_event is not None else threading.Event()
        self._input_driver = input_driver
        self._check_cancelled()
        _require_pseudoconsole_support()
        columns, rows = terminal_size
        self.normalizer.resize(columns, rows)
        # Marshaling first: an input Windows cannot carry is rejected before anything is
        # allocated, and long before there is a process to truncate a command line for.
        command_line = build_command_line(command)
        directory = validate_working_directory(cwd)
        environment = build_environment_block(self._child_env(env))
        self._start_session(command_line, directory, environment, columns, rows, time.monotonic() + spawn_timeout)

    def poll(self) -> Optional[int]:
        owner = self._owner
        if owner is None:
            return None
        code = owner.session.poll_exit_code()
        if code is not None:
            # The execution outcome is observed, so no client is left to answer.
            self.query_responder.quiesce()
        return code

    def read_output(self) -> str:
        with self._output_lock:
            text = "".join(self._decoded)
            self._decoded.clear()
            return text

    def read_raw_output(self) -> bytes:
        with self._output_lock:
            data = bytes(self._raw)
            self._raw.clear()
            return data

    def normalized_output(self) -> str:
        return self.normalizer.text()

    @property
    def terminal_reply_failed(self) -> bool:
        return self.query_responder.reply_failed

    def terminal_reply_detail(self) -> str:
        return self.query_responder.failure_detail()

    def write_input(self, data: bytes) -> InputWriteResult:
        result, _ = self._input_queue.submit(data)
        return result

    def infrastructure_failure(self) -> Optional[str]:
        detail = super().infrastructure_failure()
        if detail is not None:
            return detail
        writer = self._writer
        if writer is not None and writer.failed.is_set():
            return f"the terminal input writer failed: {writer.exc!r}"
        owner = self._owner
        if owner is not None and owner.session.failure is not None:
            # A native wait, exit-code read, job query or job termination that failed: the
            # run cannot be described by an exit status nobody could read.
            return owner.session.failure
        return None

    def terminate_tree(self, grace: float = SIGTERM_GRACE_PERIOD_SECONDS) -> None:
        """Graceful control byte, then the job. The grace period is never skipped silently:
        an undelivered control byte escalates immediately, a delivered one is given its own
        fresh interval."""
        self.query_responder.quiesce()
        owner = self._owner
        if owner is None:
            return
        self._shutdown(None if owner.session.poll_exit_code() is not None else grace)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.query_responder.quiesce()  # before either input pump stops
        try:
            self._shutdown(None)
        finally:
            self.normalizer.finalize()
        owner = self._owner
        if owner is not None:
            owner.stack.close()  # only ever after the teardown has completed
            owner.armed = False
            # Joined after the stack close, because that is what releases the last write
            # handle a reader parked on an early failure path is still waiting for.
            if not owner.session.join_reader(DRAIN_DEADLINE_SECONDS):
                self._publish_reader_stall()

    # ------------------------------------------------------------ spawn sequence

    def _start_session(
        self,
        command_line: str,
        directory: Optional[str],
        environment: str,
        columns: int,
        rows: int,
        deadline: float,
    ) -> None:
        stack = ExitStack()  # opens before the first allocation
        in_pair, out_pair = _PipePair(), _PipePair()
        in_r = _Holder(pair=in_pair)
        out_w = _Holder(pair=out_pair)
        attrs = _AttrList()
        proc = _ProcInfo()
        bundle = _ReaderHandles(out_pair)
        session = _SessionBundle(out_w, in_pair, self._input_queue)
        gate = threading.Event()
        # Every owner is registered before the API that fills it, in reverse of unwind order.
        for holder in (in_r, out_w):
            stack.callback(holder.close_if_owned)
        stack.callback(bundle.close_if_owner_is_parent)
        stack.callback(attrs.dispose_if_owned)
        # The session teardown is deliberately not a stack callback: it is the one step that
        # can time out and transfer ownership, which a callback cannot do mid-unwind.
        owner = _SessionOwner(session, stack)
        self._owner = owner  # stored early; harmless while armed

        try:
            _create_pipe(in_r, session.in_w, in_pair)
            _create_pipe(bundle, out_w, out_pair)

            self._start_reader(session, bundle, gate)
            self._start_writer(session, deadline)
            self._check_spawn_interrupted()

            _create_job(session)
            job = session.hJob
            assert job is not None  # _create_job stores one or raises
            _set_kill_on_job_close(job)

            in_read = in_r.handle()
            out_write = out_w.handle()
            assert in_read is not None and out_write is not None
            _create_pseudoconsole(session, columns, rows, in_read, out_write)

            _initialize_attribute_list(attrs, PROC_THREAD_ATTRIBUTE_COUNT)
            session.job_array[0] = session.hJob
            _update_attribute(
                attrs,
                PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
                session.hPC.value,
                ctypes.sizeof(HPCON),
                "pseudoconsole",
            )
            _update_attribute(
                attrs,
                PROC_THREAD_ATTRIBUTE_JOB_LIST,
                ctypes.addressof(session.job_array),
                ctypes.sizeof(session.job_array),
                "job list",
            )

            # The last gate before the target can run: a cancellation observed here must
            # unwind rather than let the script execute its side effects.
            self._check_spawn_interrupted()
            session.proc = proc  # attached before the call, as the reader is
            _create_process(command_line, directory, environment, attrs, proc)
            # A pump can die, and a render can be cancelled, during process creation — the
            # slowest step here — so the check runs again on the other side of it. The job
            # already holds the child, so this unwind needs no special case.
            self._check_spawn_interrupted()

            # Documented timing: the pseudoconsole owns these two now, and holding
            # outputWriteSide open means the reader never observes EOF.
            _close_handle(in_r.take())
            _close_handle(out_w.take())
            attrs.dispose()  # startup-only, retired after every check has passed
            _close_handle(proc.take_thread())

            owner.armed = False  # the commit
        except BaseException:
            if session.teardown(None):  # before any stack unwinding starts
                raise _unreleased_session_error(
                    "could not be released within its bound", self._transfer_to_finalizer(owner)
                )
            raise
        finally:
            if owner.armed:
                stack.close()  # releases exactly what never transferred

    def _start_reader(self, session: _SessionBundle, bundle: _ReaderHandles, gate: threading.Event) -> None:
        """Starts before the pseudoconsole exists, because its teardown depends on a drainer.

        The thread is attached to the session before the commit, so every failure between
        here and `CreateProcessW` unwinds with a reader the teardown can still join.
        """
        reader = threading.Thread(
            target=self._reader_main, args=(session, bundle, gate), name="codeplain-conpty-reader", daemon=True
        )
        try:
            session.reader = reader
            reader.start()
            bundle.owner = _OWNER_READER  # the commit: one assignment, nothing after it
        finally:
            gate.set()  # always: an unopened gate parks the thread forever

    def _start_writer(self, session: _SessionBundle, deadline: float) -> None:
        """Gate protocol: the writer publishes its native id and parks, the creator opens a
        thread handle while the gate still holds it, and the gate is released with a decision
        on every path."""
        writer = InputWriter(session.in_queue, _PseudoconsoleInput(session))
        session.writer = writer
        self._writer = writer
        decision = GateDecision.ABORT  # initialized before any fallible step
        try:
            writer.start()
            native_id = writer.await_ready(deadline, self._check_spawn_interrupted)
            if native_id is None:
                raise TerminalEnvironmentError(
                    f"The terminal input writer did not start: {writer.exc!r}"
                    if writer.failed.is_set()
                    else "The terminal input writer did not report itself before the spawn deadline."
                )
            _open_thread_handle(session.writer_handle, native_id)
            decision = GateDecision.RUN  # only after the handle is stored
        finally:
            writer.gate.set(decision)  # always: ABORT wakes the writer to exit untouched

    def _reader_main(self, session: _SessionBundle, bundle: _ReaderHandles, gate: threading.Event) -> None:
        gate.wait()
        if bundle.owner != _OWNER_READER:
            return  # the parent still owns everything; touch nothing, publish nothing
        session.adopt_reader_thread()  # before the first read, so teardown can always cancel it
        reader_exc: Optional[BaseException] = None
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        handle = bundle.out_r.value
        try:
            self._reader_loop(handle, decoder)
        except BaseException as exc:  # nothing here reaches threading.excepthook
            reader_exc = exc
        finally:
            # Cleanup before publication: a rollback that sees the failure flag can rely on
            # the read handle already being closed, which is the branch that makes
            # ClosePseudoConsole() safe without a drainer.
            _close_handle(bundle.take())
            try:
                self._flush_decoder(decoder)
                self.normalizer.finalize()
            except BaseException as exc:  # finalization can fail too
                reader_exc = reader_exc or exc
            finally:
                self.reader_exc = reader_exc  # stored while still unobservable
                if reader_exc is not None:
                    self.reader_failed.set()

    def _reader_loop(self, handle: Optional[int], decoder) -> None:
        if not handle:
            return
        buffer = ctypes.create_string_buffer(READ_CHUNK_BYTES)
        read = DWORD(0)
        while True:
            ok = kernel32.ReadFile(handle, buffer, READ_CHUNK_BYTES, ctypes.byref(read), None)
            if not ok:
                error = ctypes.get_last_error()
                if error in (ERROR_BROKEN_PIPE, ERROR_HANDLE_EOF, ERROR_OPERATION_ABORTED):
                    return  # the pseudoconsole released its end
                raise _win_error("Reading the script's terminal output", error)
            if read.value == 0:
                return
            self._feed_output(buffer.raw[: read.value], decoder)

    def _feed_output(self, chunk: bytes, decoder) -> None:
        text = decoder.decode(chunk)
        with self._output_lock:
            self._raw += chunk
            if text:
                self._decoded.append(text)
        self._byte_sink(chunk)  # outside the output lock: parsing must not block read_output()

    def _flush_decoder(self, decoder) -> None:
        tail = decoder.decode(b"", final=True)  # a trailing partial sequence becomes U+FFFD
        if tail:
            with self._output_lock:
                self._decoded.append(tail)

    # ------------------------------------------------------------------ internals

    def _admit_reply(self, payload: bytes, on_complete: Callable[[Optional[str]], None]) -> None:
        """One non-blocking whole-item admission of a terminal reply, from the reader.

        Replies are ordinary data admissions. The reserve exists so the graceful control byte
        can always be posted; a target that queries in a loop against a blocked input pipe
        would otherwise fill the whole budget with replies and leave cancellation with nothing
        but forced termination. A rejected reply is recorded as undelivered, which is the
        outcome the responder exists to report.
        """
        self._input_queue.submit(payload, on_resolve=reply_resolution(on_complete))

    def _child_env(self, env: Optional[dict]) -> dict:
        child_env = child_environment(env)
        term = child_env.get("TERM")
        child_env["TERM"] = term if term else DEFAULT_TERM
        # git reads the console directly, so no redirection can reach a credential prompt;
        # failing is the only bounded outcome.
        child_env["GIT_TERMINAL_PROMPT"] = "0"
        return child_env

    def _shutdown(self, grace: Optional[float]) -> None:
        owner = self._owner
        if owner is None:
            return
        if owner.session.teardown(grace):
            raise _unreleased_session_error("did not shut down within its bound", self._transfer_to_finalizer(owner))

    def _transfer_to_finalizer(self, owner: _SessionOwner) -> bool:
        """Publishes the owner to the daemon finalizer, or keeps it here if none took it.

        Disarming comes first, because `finally` runs on this path too and must not unwind a
        stack the finalizer now holds. Clearing `self._owner` comes last, and only once
        another owner exists: a failed `Thread.start()` would otherwise drop the only
        reference to a live job, pseudoconsole and input handle.
        """
        owner.armed = False
        if _hand_off_to_finalizer(owner):
            self._owner = None
            return True
        # Nobody can finish this later, so release what is provably safe to release now and
        # keep the rest recorded on the session that stays reachable from this backend.
        owner.session.record_failure("no finalizer thread could be started for the terminal session")
        owner.session.release_abandoned()
        owner.stack.close()
        return False

    def _check_cancelled(self) -> None:
        if self._stop_event.is_set():
            raise RenderCancelledError()

    def _check_pumps(self) -> None:
        detail = self.infrastructure_failure()
        if detail is not None:
            raise TerminalEnvironmentError(f"The terminal backend failed while starting the script: {detail}")

    def _check_spawn_interrupted(self) -> None:
        self._check_cancelled()
        self._check_pumps()
