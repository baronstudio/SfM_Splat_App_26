"""proc.py — child-process supervision shared by every exe-driven step.

Pure module: no FastAPI import and no database import, so the steps stay
callable from tests (CLAUDE.md §2.4).

Why this exists: the steps stream their tool's output while it runs, so the
coroutine is parked for the whole run. `/api/pipeline/control abort` cancels
that task, which raises `CancelledError` at the await point and marks the step
aborted in the UI — but it never touches the child. A training aborted that way
left the tool holding the GPU with nothing in the process holding a reference to
it: Task Manager was the only way out. Every child is therefore registered here,
keyed by the project directory it works in, and abort kills the tree from the
outside.

**The output goes to a file rather than a pipe when a run context says where
(TODO P7.2), and that is what lets a run outlive the backend.** A pipe cannot
outlive its reader: `--reload` firing or the Backend window closing took the
task with it, and the tool was then orphaned on the GPU with its transcript
gone. A file is read by whoever comes next — measured 2026-09-03, a process
holding no handle on the file tails what a *foreign* child writes into it, 28
reads over 6 s at a 75 ms median lag — so the transcript is durable, the step's
parser can be replayed over it from byte 0, and the reader is replaceable.

Three things follow, all measured before they were relied on:

* The child is spawned `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`, so
  closing the console it was started from does not send it `CTRL_CLOSE_EVENT`
  and a Ctrl-C in that console does not reach it.
* A process this module never spawned can be **adopted** from a pid alone:
  `OpenProcess` + `QueryFullProcessImageNameW` + `GetProcessTimes` identify it
  (the creation time is what makes a recycled pid impossible to mistake for
  it), `GetExitCodeProcess` reads `STILL_ACTIVE` or the real code, and
  `WaitForSingleObject` waits for it. Measured: a child outlived its parent and
  a second process read its exit code **7**.
* `taskkill /F /T` reaches an adopted tree exactly as it reaches a spawned one,
  because it works on the pid and not on a handle we hold.
"""

from __future__ import annotations

import asyncio
import contextvars
import ctypes
import os
import re
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Callable, Optional, Sequence

_IS_WINDOWS = sys.platform == "win32"

# Tools colour their output with SGR escapes; they are noise in the LiveLog and
# they break any level classification done on the text.
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# project key -> live children of that project's run
_LIVE: dict[str, set] = {}
# pids this module killed, so a step can tell "the user stopped it" from
# "the tool died on its own" — both surface as a non-zero return code.
_KILLED: set[int] = set()

# How often the file reader looks for new output. 50 ms against channels that
# print twice a second (FFmpeg's `-progress`) or every 1.5 s (the trainer's bar
# line at 100-step intervals), so it costs nothing anybody can see; the probe
# above measured the whole path at a 75 ms median lag with a 200 ms poll.
_TAIL_POLL_S = 0.05

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000
_INFINITE = 0xFFFFFFFF


class ProcessAborted(RuntimeError):
    """A child was killed by the user, not by its own failure.

    The runner treats this like `AnalysisAborted`: the step is `aborted`, not
    `error`, and the project keeps a clean `error_message`.
    """


def _key(project_path: Path | str) -> str:
    return str(Path(project_path).resolve()).lower()


# ── process identity, for a pid and nothing else ─────────────────────────────

if _IS_WINDOWS:
    import ctypes.wintypes as _w

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def _open_process(pid: int):
        handle = _k32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE, False, int(pid)
        )
        return handle or None

    def _close_handle(handle) -> None:
        if handle:
            _k32.CloseHandle(handle)

    def _image_of(handle) -> str:
        size = _w.DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if not _k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return Path(buf.value).name.lower()

    def _created_of(handle) -> int:
        creation, exited = _w.FILETIME(), _w.FILETIME()
        kernel, user = _w.FILETIME(), _w.FILETIME()
        if not _k32.GetProcessTimes(
            handle, ctypes.byref(creation), ctypes.byref(exited),
            ctypes.byref(kernel), ctypes.byref(user),
        ):
            return 0
        return (creation.dwHighDateTime << 32) | creation.dwLowDateTime

    def _exit_code_of(handle) -> Optional[int]:
        code = _w.DWORD()
        if not _k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return None
        return code.value

    def _wait_handle(handle, timeout: Optional[float]) -> None:
        ms = _INFINITE if timeout is None else int(timeout * 1000)
        _k32.WaitForSingleObject(handle, ms)

else:  # pragma: no cover — this app is Windows-first (CLAUDE.md §3)
    def _open_process(pid: int):
        try:
            os.kill(int(pid), 0)
        except OSError:
            return None
        return int(pid)

    def _close_handle(handle) -> None:
        return None

    def _image_of(handle) -> str:
        return ""

    def _created_of(handle) -> int:
        return 0

    def _exit_code_of(handle) -> Optional[int]:
        try:
            os.kill(int(handle), 0)
        except OSError:
            return 0
        return _STILL_ACTIVE

    def _wait_handle(handle, timeout: Optional[float]) -> None:
        import time as _time
        deadline = None if timeout is None else _time.monotonic() + timeout
        while _exit_code_of(handle) == _STILL_ACTIVE:
            if deadline is not None and _time.monotonic() > deadline:
                return
            _time.sleep(0.05)


def process_identity(pid: int) -> Optional[tuple[str, int]]:
    """`(image name, creation time)` for a live pid, or None if it is gone."""
    handle = _open_process(pid)
    if not handle:
        return None
    try:
        return _image_of(handle), _created_of(handle)
    finally:
        _close_handle(handle)


def process_is(pid: Optional[int], image: Optional[str], created: Optional[int]) -> bool:
    """Is this pid still the process it was?

    Both halves matter. The pid says something is there; the image name and the
    **creation time** say it is the same something — a pid is recycled by the OS
    and a backend that came back an hour later must not taskkill whatever now
    holds the number.
    """
    if not pid:
        return False
    identity = process_identity(pid)
    if identity is None:
        return False
    live_image, live_created = identity
    if image and live_image and live_image != image.lower():
        return False
    if created and live_created and live_created != created:
        return False
    return True


# ── the run context: where this run's output goes, and what to adopt ─────────

@dataclass
class RunContext:
    """What `spawn` needs to know about the run it is being called inside.

    Installed by `pipeline_runner` around a run, so the steps never learn that a
    job record exists (§2.4's injection, kept). Two jobs:

    * `tool_log` is where the child's output goes instead of into a pipe.
    * `adopt_pid` is a live child from a previous process to attach to rather
      than start. It is consumed by the first `spawn`, so a step that runs more
      than one command behaves normally for the rest of them.
    """

    tool_log: Optional[Path] = None
    adopt_pid: Optional[int] = None
    adopt_image: Optional[str] = None
    adopt_created: Optional[int] = None
    #: called with (pid, image, created) after a real spawn, to record it
    on_spawn: Optional[Callable[[int, str, int], None]] = None
    #: how many commands this run has started, for the adoption guard
    spawns: int = field(default=0)


_RUN_CTX: contextvars.ContextVar[Optional[RunContext]] = contextvars.ContextVar(
    "proc_run_context", default=None
)


def set_run_context(ctx: Optional[RunContext]):
    """Install the run context. Returns the token to reset it with."""
    return _RUN_CTX.set(ctx)


def reset_run_context(token) -> None:
    _RUN_CTX.reset(token)


def run_context() -> Optional[RunContext]:
    return _RUN_CTX.get()


def adopting() -> bool:
    """True while a step is being replayed over an already-running child.

    Read by `project_ops.reset_steps`, which must not delete the output of the
    very run it is re-attaching to: §14.1's "re-running a step is a reset of
    that step" is exactly wrong here, because the step is not being re-run.
    """
    ctx = _RUN_CTX.get()
    return bool(ctx and ctx.adopt_pid)


class AdoptedProcess:
    """A `Popen` stand-in for a child this process never started.

    Only what the steps use: `pid`, `poll()`, `wait()`, `returncode`, and a
    `log_path` so `iter_lines` tails the transcript instead of a pipe it has no
    handle on.
    """

    def __init__(self, pid: int, log_path: Optional[Path]):
        self.pid = int(pid)
        self.log_path = log_path
        self.stdout = None
        self.returncode: Optional[int] = None
        self._handle = _open_process(pid)
        self._log_handle = None

    def poll(self) -> Optional[int]:
        if self.returncode is not None:
            return self.returncode
        if not self._handle:
            self.returncode = -1
            return self.returncode
        code = _exit_code_of(self._handle)
        if code is None or code == _STILL_ACTIVE:
            return None
        self.returncode = code
        return code

    def wait(self, timeout: Optional[float] = None) -> Optional[int]:
        if self.poll() is None and self._handle:
            _wait_handle(self._handle, timeout)
        return self.poll()

    def close(self) -> None:
        _close_handle(self._handle)
        self._handle = None


def spawn(
    cmd: Sequence[str],
    project_path: Path,
    **popen_kwargs,
) -> subprocess.Popen | AdoptedProcess:
    """Start a tool — or attach to the one already running — abort-killable.

    `subprocess.Popen` + a reader rather than `asyncio.create_subprocess_exec`,
    which raises `NotImplementedError` when uvicorn runs on a Windows
    SelectorEventLoop.

    With a run context (the normal path since P7.2) the output is **appended to
    that run's tool log** rather than piped, and the pid is recorded: that is
    what makes the run survive this process. A step that starts several commands
    appends all of them to the one transcript, in order, which is what its
    parser reads anyway.
    """
    ctx = _RUN_CTX.get()

    if ctx and ctx.adopt_pid:
        pid, image, created = ctx.adopt_pid, ctx.adopt_image, ctx.adopt_created
        # Consumed: only the first command of a replayed step is the live one.
        ctx.adopt_pid = None
        if process_is(pid, image, created):
            proc = AdoptedProcess(pid, ctx.tool_log)
            _LIVE.setdefault(_key(project_path), set()).add(proc)
            return proc
        # It finished between the plan and here. Fall through and start it: the
        # step is being re-entered from the top, so that is the correct answer.

    if ctx and ctx.tool_log:
        ctx.tool_log.parent.mkdir(parents=True, exist_ok=True)
        # Append, never truncate: several commands in one step share the
        # transcript, and the runner is what clears it at the start of a run.
        handle = open(ctx.tool_log, "ab")
        popen_kwargs["stdout"] = handle
        popen_kwargs["stderr"] = subprocess.STDOUT
        if _IS_WINDOWS:
            popen_kwargs.setdefault(
                "creationflags", DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            )
    else:
        handle = None
        popen_kwargs.setdefault("stdout", subprocess.PIPE)
        popen_kwargs.setdefault("stderr", subprocess.STDOUT)

    if not _IS_WINDOWS:
        # Own process group, so a kill reaches whatever the tool itself spawned.
        popen_kwargs.setdefault("start_new_session", True)

    proc = subprocess.Popen(list(cmd), **popen_kwargs)
    if handle is not None:
        # The child holds its own duplicate of the handle; ours is only needed
        # to hand it over, and leaving it open would pin the file.
        proc.log_path = ctx.tool_log  # type: ignore[attr-defined]
        proc._log_handle = handle  # type: ignore[attr-defined]
    _LIVE.setdefault(_key(project_path), set()).add(proc)

    if ctx:
        ctx.spawns += 1
        if ctx.on_spawn:
            identity = process_identity(proc.pid) or ("", 0)
            try:
                ctx.on_spawn(proc.pid, identity[0], identity[1])
            except Exception:  # noqa: BLE001 — recording is never worth the run
                pass
    return proc


def release(project_path: Path, proc) -> bool:
    """Deregister a finished child. Returns True if *we* killed it.

    Call it from a `finally`: an unreleased entry would make the next abort
    taskkill a pid that has since been recycled by the OS.
    """
    children = _LIVE.get(_key(project_path))
    if children:
        children.discard(proc)
        if not children:
            _LIVE.pop(_key(project_path), None)

    handle = getattr(proc, "_log_handle", None)
    if handle is not None:
        try:
            handle.close()
        except Exception:  # noqa: BLE001
            pass
        proc._log_handle = None
    if isinstance(proc, AdoptedProcess):
        proc.close()

    killed = proc.pid in _KILLED
    _KILLED.discard(proc.pid)
    return killed


def was_killed(proc) -> bool:
    """True if this child was killed through `kill_tree` / abort."""
    return proc.pid in _KILLED


def kill_tree(proc) -> bool:
    """Kill a child and everything it spawned. Idempotent, never raises.

    `taskkill /F /T` and not `Popen.kill()`: these tools are launchers as much
    as workers — `spirula geometry` shells out to `curl` for its checkpoint — and
    killing only the parent orphans the grandchild that actually holds the work.
    It also works on an adopted pid, which is the point: nothing here needs a
    handle this process opened.
    """
    if proc.poll() is not None:
        return False

    _KILLED.add(proc.pid)
    _kill_pid(proc.pid)
    try:
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        pass
    return True


def _kill_pid(pid: int) -> None:
    try:
        if _IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=15,
            )
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001
        pass


def kill_orphan(pid: Optional[int], image: Optional[str], created: Optional[int]) -> bool:
    """Kill a tree left by a previous process — by pid, with the identity checked.

    For the runs that cannot be re-attached (the in-process passes, whose numpy
    state died with the backend): the tool is still working for a result nobody
    will ever collect, so leaving it on the GPU is worse than stopping it.
    """
    if not process_is(pid, image, created):
        return False
    _kill_pid(int(pid))
    return True


def kill_project_children(project_path: Path) -> int:
    """Kill every tool still running for this project. Returns how many."""
    children = list(_LIVE.get(_key(project_path), ()))
    return sum(1 for proc in children if kill_tree(proc))


def live_count(project_path: Path) -> int:
    """How many tools this project currently has running (debug/tests)."""
    return len(_LIVE.get(_key(project_path), ()))


# ── reading the output ───────────────────────────────────────────────────────

async def iter_lines(
    proc, loop, chunk_size: int = 65536
) -> AsyncIterator[str]:
    """Yield clean lines from a child, splitting on CR as well as LF.

    `readline()` splits on LF only, and several tools here redraw a status line
    with a bare carriage return instead: FFmpeg's `frame= … time=` stats and the
    `curl` bar of `spirula geometry`'s checkpoint fetch (§7.5). A plain
    readline() therefore swallows a whole run into one multi-megabyte "line"
    that arrives when the process exits — which is exactly when the progress it
    carries has stopped being useful.

    Two sources, one interface: a file when the run has a transcript (P7.2), a
    pipe otherwise. The file path is also what makes a **replay** possible — an
    adopted run reads its own transcript from byte 0, at speed, through the same
    parser, and slides into live tailing when it catches up.
    """
    log_path = getattr(proc, "log_path", None)
    if log_path is not None:
        async for line in _iter_file(proc, Path(log_path), chunk_size):
            yield line
        return
    async for line in _iter_pipe(proc, loop, chunk_size):
        yield line


async def _iter_file(proc, path: Path, chunk_size: int) -> AsyncIterator[str]:
    """Tail a transcript while it is being written, from the beginning.

    Every read is synchronous and tiny; the await is the sleep between them, so
    unlike the pipe reader this parks no thread and offers a cancellation point
    on every pass — which is what makes an abort land promptly on a step whose
    tool has gone quiet.
    """
    buffer = ""
    position = 0
    handle = None
    try:
        while True:
            if handle is None:
                if not path.exists():
                    if proc.poll() is not None:
                        break
                    await asyncio.sleep(_TAIL_POLL_S)
                    continue
                handle = open(path, "rb")

            handle.seek(position)
            chunk = handle.read(chunk_size)
            position = handle.tell()

            if chunk:
                buffer += chunk.decode("utf-8", errors="replace")
                parts = re.split(r"[\r\n]", buffer)
                buffer = parts.pop()
                for part in parts:
                    line = _ANSI.sub("", part).strip()
                    if line:
                        yield line
                # Never sleep while there is more to read: this is also the
                # replay path, and a 500 KB transcript is a handful of passes.
                continue

            if proc.poll() is not None:
                # One last look: the child can write between the read above and
                # the exit that was observed after it.
                handle.seek(position)
                chunk = handle.read()
                position = handle.tell()
                if chunk:
                    buffer += chunk.decode("utf-8", errors="replace")
                    parts = re.split(r"[\r\n]", buffer)
                    buffer = parts.pop()
                    for part in parts:
                        line = _ANSI.sub("", part).strip()
                        if line:
                            yield line
                    continue
                break

            await asyncio.sleep(_TAIL_POLL_S)
    finally:
        if handle is not None:
            handle.close()

    line = _ANSI.sub("", buffer).strip()
    if line:
        yield line


async def _iter_pipe(proc, loop, chunk_size: int) -> AsyncIterator[str]:
    """The pipe reader, unchanged — for a spawn with no run context.

    Each read blocks in the thread pool, so the caller's coroutine is
    cancellable at the await and the child is killed by `kill_tree`, never by
    unwinding this generator.
    """
    buffer = ""
    while True:
        chunk = await loop.run_in_executor(None, proc.stdout.read1, chunk_size)
        if not chunk:
            break
        buffer += chunk.decode("utf-8", errors="replace")
        parts = re.split(r"[\r\n]", buffer)
        buffer = parts.pop()
        for part in parts:
            line = _ANSI.sub("", part).strip()
            if line:
                yield line
    line = _ANSI.sub("", buffer).strip()
    if line:
        yield line
