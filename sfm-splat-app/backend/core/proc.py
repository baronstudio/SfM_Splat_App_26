"""proc.py — child-process supervision shared by every exe-driven step.

Pure module: no FastAPI import, so the steps stay callable from tests
(CLAUDE.md §2.4).

Why this exists: the steps stream their tool's stdout from a thread-pool
`readline()`, so the coroutine is parked in `run_in_executor` for the whole run.
`/api/pipeline/control abort` cancels that task, which raises `CancelledError` at
the await point and marks the step aborted in the UI — but it never touches the
child. A training aborted that way left `LichtFeld-Studio.exe` holding the GPU
with nothing in the process holding a reference to it: Task Manager was the only
way out, and the executor thread stayed blocked on a pipe that never closed.

Every child is therefore registered here, keyed by the project directory it works
in, and abort kills the tree from the outside. Killing it closes the pipe, which
is also what unblocks the reader thread.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import AsyncIterator, Sequence

_IS_WINDOWS = sys.platform == "win32"

# Tools colour their output with SGR escapes; they are noise in the LiveLog and
# they break any level classification done on the text.
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# project key -> live children of that project's run
_LIVE: dict[str, set[subprocess.Popen]] = {}
# pids this module killed, so a step can tell "the user stopped it" from
# "the tool died on its own" — both surface as a non-zero return code.
_KILLED: set[int] = set()


class ProcessAborted(RuntimeError):
    """A child was killed by the user, not by its own failure.

    The runner treats this like `AnalysisAborted`: the step is `aborted`, not
    `error`, and the project keeps a clean `error_message`.
    """


def _key(project_path: Path | str) -> str:
    return str(Path(project_path).resolve()).lower()


def spawn(
    cmd: Sequence[str],
    project_path: Path,
    **popen_kwargs,
) -> subprocess.Popen:
    """Start a tool with its output merged onto one pipe, abort-killable.

    `subprocess.Popen` + a reader in the executor rather than
    `asyncio.create_subprocess_exec`, which raises `NotImplementedError` when
    uvicorn runs on a Windows SelectorEventLoop.
    """
    popen_kwargs.setdefault("stdout", subprocess.PIPE)
    popen_kwargs.setdefault("stderr", subprocess.STDOUT)
    if not _IS_WINDOWS:
        # Own process group, so a kill reaches whatever the tool itself spawned.
        popen_kwargs.setdefault("start_new_session", True)

    proc = subprocess.Popen(list(cmd), **popen_kwargs)
    _LIVE.setdefault(_key(project_path), set()).add(proc)
    return proc


def release(project_path: Path, proc: subprocess.Popen) -> bool:
    """Deregister a finished child. Returns True if *we* killed it.

    Call it from a `finally`: an unreleased entry would make the next abort
    taskkill a pid that has since been recycled by the OS.
    """
    children = _LIVE.get(_key(project_path))
    if children:
        children.discard(proc)
        if not children:
            _LIVE.pop(_key(project_path), None)
    killed = proc.pid in _KILLED
    _KILLED.discard(proc.pid)
    return killed


def was_killed(proc: subprocess.Popen) -> bool:
    """True if this child was killed through `kill_tree` / abort."""
    return proc.pid in _KILLED


def kill_tree(proc: subprocess.Popen) -> bool:
    """Kill a child and everything it spawned. Idempotent, never raises.

    `taskkill /F /T` and not `Popen.kill()`: RealityScan and LichtFeld Studio
    are launchers as much as workers, and killing only the parent orphans the
    grandchild that actually holds the GPU — and leaves it holding the stdout
    pipe, so the reader thread never sees EOF.
    """
    if proc.poll() is not None:
        return False

    _KILLED.add(proc.pid)
    try:
        if _IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=15,
            )
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

    try:
        proc.wait(timeout=5)
    except Exception:
        pass
    return True


def kill_project_children(project_path: Path) -> int:
    """Kill every tool still running for this project. Returns how many."""
    children = list(_LIVE.get(_key(project_path), ()))
    return sum(1 for proc in children if kill_tree(proc))


def live_count(project_path: Path) -> int:
    """How many tools this project currently has running (debug/tests)."""
    return len(_LIVE.get(_key(project_path), ()))


async def iter_lines(
    proc: subprocess.Popen, loop, chunk_size: int = 4096
) -> AsyncIterator[str]:
    """Yield clean lines from a child, splitting on CR as well as LF.

    `readline()` splits on LF only, and every tool in this pipeline redraws a
    status line with a bare carriage return instead: LichtFeld Studio's training
    bar, FFmpeg's `frame= … time=` stats, and RealityScan's log tail. A plain
    readline() therefore swallows the whole run into one multi-megabyte "line"
    that arrives when the process exits — which is exactly when the progress it
    carries has stopped being useful.

    Lives here rather than in one step because that is the third place the same
    defect appeared. Each read blocks in the thread pool, so the caller's
    coroutine is cancellable at the await and the child is killed by
    `kill_tree`, never by unwinding this generator.
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
