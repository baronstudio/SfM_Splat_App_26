"""Viewer previews: which file a step produced, and its browser-sized copy.

The 3D viewer never loads a step's output directly. `rc_output/pointcloud.ply`
is 18 MB of ASCII and `lfs_output/splat_*.ply` has been measured at 1.24 GB -
so each source gets a decimated binary preview under `projects/<slug>/preview/`,
served by the existing `/static` mount and rebuilt only when the source moves.

One preview per (source, level): the UI opens at the default level and can ask
for a bigger one - up to the whole file - without throwing away the small one
it is already showing.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Callable, Optional

from backend.core import ply

PREVIEW_DIRNAME = "preview"

# Where each wizard step leaves the thing worth looking at.
SOURCE_DIRS: dict[str, str] = {
    "rc": "rc_output",
    "lfs": "lfs_output",
    "export": "export",
}

# rc_output holds one known filename; the other two are scanned.
SOURCE_NAMES: dict[str, tuple[str, ...]] = {
    "rc": ("pointcloud.ply",),
}

_SUFFIXES = (".ply", ".splat")

ProgressFn = Callable[[float], None]


class PreviewError(RuntimeError):
    """The preview could not be built - message is meant for the UI."""


# -- Source discovery --------------------------------------------------------

def find_source(project_path: Path, source: str) -> Optional[Path]:
    """The file a given step left behind, or None.

    Newest wins, not largest: with several `splat_<iter>.ply` checkpoints in
    `lfs_output/`, the last one written is the last one trained, and a bigger
    file from an earlier run is not a better answer.
    """
    dirname = SOURCE_DIRS.get(source)
    if dirname is None:
        raise PreviewError(f"Unknown preview source {source!r}")
    directory = project_path / dirname
    if not directory.is_dir():
        return None

    known = SOURCE_NAMES.get(source)
    if known:
        for name in known:
            candidate = directory / name
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate

    candidates = [
        f for f in directory.iterdir()
        if f.is_file() and f.suffix.lower() in _SUFFIXES and f.stat().st_size > 0
    ]
    if not candidates:
        return None
    # .ply before .splat at equal recency: LFS writes both, and only the PLY
    # carries the spherical harmonics.
    candidates.sort(key=lambda f: (f.stat().st_mtime, f.suffix.lower() == ".ply"))
    return candidates[-1]


# -- Cache bookkeeping -------------------------------------------------------

def _level_tag(max_count: Optional[int]) -> str:
    return "full" if not max_count or max_count <= 0 else str(int(max_count))


def _fingerprint(src: Path) -> str:
    """Eight hex digits standing for *this* revision of the source file."""
    stat = src.stat()
    seed = f"{src.name}:{stat.st_mtime_ns}:{stat.st_size}".encode("utf-8")
    return hashlib.blake2b(seed, digest_size=4).hexdigest()


def _target(project_path: Path, source: str, kind: str, max_count: Optional[int],
            fingerprint: str) -> Path:
    """Where the preview for one revision of one source, at one level, lives.

    The fingerprint is in the *name* on purpose. A rebuild used to write over
    the previous file, and on Windows that is a rename onto a handle somebody
    else still holds: the viewer aborts its download whenever the level changes
    or the canvas unmounts, and a cancelled `FileResponse` leaks the open file
    (its `aclose()` is a thread call, and a cancelled task never reaches it) -
    so `os.replace` came back `[WinError 5] Access denied` and the preview was
    stuck until the server restarted. A new revision now writes a new name;
    the stale one is pruned if the OS lets go of it, and costs 16 MB if not.
    It also gives the browser a URL it cannot serve a previous cloud from.
    """
    suffix = ".splat" if kind == ply.KIND_SPLAT else ".pc3d"
    name = f"{source}_{_level_tag(max_count)}_{fingerprint}{suffix}"
    return project_path / PREVIEW_DIRNAME / name


def _prune_siblings(target: Path, source: str, max_count: Optional[int]) -> None:
    """Drop the previews of earlier revisions at the same (source, level).

    Best effort: a file another process still holds open cannot be unlinked on
    Windows, and that must not fail a build that has already succeeded.
    """
    keep = {target.name, _stamp_path(target).name}
    prefix = f"{source}_{_level_tag(max_count)}"
    directory = target.parent
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    for path in entries:
        name = path.name
        if name in keep or not path.is_file():
            continue
        # `<prefix>_<fingerprint>.<ext>` today, `<prefix>.<ext>` before it.
        if not (name.startswith(prefix + "_") or name.startswith(prefix + ".")):
            continue
        try:
            os.unlink(path)
        except OSError:
            pass


def _stamp_path(target: Path) -> Path:
    return target.with_name(target.name + ".json")


def _read_stamp(target: Path) -> Optional[dict]:
    try:
        return json.loads(_stamp_path(target).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _is_fresh(target: Path, src: Path) -> bool:
    """A preview is stale as soon as its source is re-written, never by age."""
    if not target.is_file():
        return False
    stamp = _read_stamp(target)
    if not stamp:
        return False
    stat = src.stat()
    return (
        stamp.get("source_file") == src.name
        and int(stamp.get("source_mtime_ns", -1)) == stat.st_mtime_ns
        and int(stamp.get("source_bytes", -1)) == stat.st_size
    )


# -- Status / build ----------------------------------------------------------

def status(project_path: Path, slug: str, source: str,
           max_count: Optional[int]) -> dict:
    """Everything the viewer needs to decide what to do, without building.

    Reads the source header only - a few kilobytes even in front of a 1.24 GB
    splat - so this stays cheap enough to poll.
    """
    src = find_source(project_path, source)
    if src is None:
        return {
            "source": source, "available": False, "ready": False,
            "max_count": max_count,
        }

    stat = src.stat()
    base = {
        "source": source,
        "available": True,
        "source_file": src.name,
        "source_bytes": stat.st_size,
        "source_url": f"/static/{slug}/{SOURCE_DIRS[source]}/{src.name}",
        "max_count": max_count,
        "ready": False,
    }
    try:
        described = ply.describe(src)
    except (ply.PlyError, OSError) as exc:
        base["error"] = str(exc)
        return base

    base.update(kind=described["kind"], total=described["total"])
    target = _target(project_path, source, described["kind"], max_count,
                     _fingerprint(src))
    if _is_fresh(target, src):
        stamp = _read_stamp(target) or {}
        base.update(
            ready=True,
            url=f"/static/{slug}/{PREVIEW_DIRNAME}/{target.name}",
            count=stamp.get("count", described["total"]),
            bytes=stamp.get("bytes", target.stat().st_size),
            decimated=bool(stamp.get("decimated", False)),
        )
    return base


def build(project_path: Path, slug: str, source: str, max_count: Optional[int],
          progress: Optional[ProgressFn] = None) -> dict:
    """Build (or reuse) the preview for one source at one level. Blocking."""
    src = find_source(project_path, source)
    if src is None:
        raise PreviewError(f"No {source} output to preview yet.")

    described = ply.describe(src)
    target = _target(project_path, source, described["kind"], max_count,
                     _fingerprint(src))
    if _is_fresh(target, src):
        return status(project_path, slug, source, max_count)

    try:
        meta = ply.convert(src, target, max_count, progress)
    except (ply.PlyError, OSError, ValueError) as exc:
        raise PreviewError(f"{src.name}: {exc}") from exc

    stat = src.stat()
    _stamp_path(target).write_text(
        json.dumps({
            "source_file": src.name,
            "source_mtime_ns": stat.st_mtime_ns,
            "source_bytes": stat.st_size,
            "max_count": max_count,
            **meta,
        }, indent=2),
        encoding="utf-8",
    )
    _prune_siblings(target, source, max_count)
    return status(project_path, slug, source, max_count)


# -- One build at a time, per (project, source, level) -----------------------

class _Builds:
    """In-flight builds, so a polling UI cannot start the same job twice.

    A build is a thread doing bulk IO, not a pipeline step: it is deliberately
    outside `pipeline_runner` and outside the abort machinery, because nothing
    it touches is a running tool and cancelling it would only leave a `.part`
    behind.
    """

    def __init__(self) -> None:
        self._jobs: dict[tuple[str, str, str], dict] = {}

    @staticmethod
    def _key(slug: str, source: str, max_count: Optional[int]) -> tuple[str, str, str]:
        return (slug, source, _level_tag(max_count))

    def get(self, slug: str, source: str, max_count: Optional[int]) -> Optional[dict]:
        return self._jobs.get(self._key(slug, source, max_count))

    def start(self, project_path: Path, slug: str, source: str,
              max_count: Optional[int]) -> dict:
        key = self._key(slug, source, max_count)
        job = self._jobs.get(key)
        if job and not job["task"].done():
            return job

        job = {"progress": 0.0, "error": None, "task": None}

        def on_progress(value: float) -> None:
            job["progress"] = value

        async def run() -> dict:
            try:
                return await asyncio.to_thread(
                    build, project_path, slug, source, max_count, on_progress
                )
            except Exception as exc:  # surfaced through the status endpoint
                job["error"] = str(exc)
                raise

        task = asyncio.get_running_loop().create_task(run())
        # Nobody awaits this task; without a done-callback a failure would be
        # reported by asyncio at garbage-collection time and nowhere else.
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        job["task"] = task
        self._jobs[key] = job
        return job


builds = _Builds()
