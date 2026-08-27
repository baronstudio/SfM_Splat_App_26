"""Close served files even when the client hangs up.

`StaticFiles` streams through `anyio.AsyncFile`, whose `aclose()` is a *thread*
call - and `anyio`'s thread runner starts with a checkpoint. Cancel the task
(any aborted download: the viewer aborts its fetch on every level change and on
every unmount, React's StrictMode double-mount included) and that checkpoint
raises `CancelledError` before the close ever runs, so the handle survives the
request and lives until the server exits.

On Linux nobody notices - a leaked read handle does not stop anything. On
Windows it stops everything that touches the file: rebuilding a preview came
back `[WinError 5] Access denied` on the rename, and deleting or resetting the
project would fail the same way on `preview/`.

Closing a file object is a fast syscall, not blocking IO worth a worker thread,
so the patch is to do it inline - which also makes it immune to cancellation.
"""

from __future__ import annotations

from anyio import AsyncFile

_patched = False


def apply_sync_close() -> None:
    """Make `AsyncFile.aclose()` close in place. Idempotent."""
    global _patched
    if _patched:
        return

    original = AsyncFile.aclose

    async def aclose(self: AsyncFile) -> None:
        fp = getattr(self, "_fp", None)
        if fp is None:  # anyio changed shape - leave it alone
            await original(self)
            return
        fp.close()

    AsyncFile.aclose = aclose  # type: ignore[method-assign]
    _patched = True
