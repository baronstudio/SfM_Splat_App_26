"""Viewer previews: which file a step produced, and its browser-sized copy.

The 3D viewer never loads a step's output directly. Step 3's sparse model is
COLMAP binary rather than a PLY, and step 4's `splat.ply` was measured at
**247 MB** for a small project (CLAUDE.md §7.9) - so each source gets a
decimated binary preview under `projects/<slug>/preview/`, served by the
existing `/static` mount and rebuilt only when the source moves.

One preview per (source, level): the UI opens at the default level and can ask
for a bigger one - up to the whole file - without throwing away the small one
it is already showing.

**Three sources, and they are found rather than named.** `sfm` is whichever
`sparse/N` the reconstruction left (`sparse/0` is the largest component), and
`train` is the checkpoint the trainer kept - `--save-only-latest-checkpoint`
defaults 1, but that is the tool's default and not a promise, so the glob takes
the highest step it finds instead of assuming there is one.

`mesh` is step 5's surface, and it is **the one source with no preview file at
all**. A textured glb is neither a point cloud nor a splat: there is no record
format to decimate it into, `three`'s own `GLTFLoader` reads it as it stands,
and the reference mesh was 11.6 MB against the 178 MB splat it came from. So
its "preview" is the source, served from the same `/static` mount, and every
piece of cache bookkeeping below is skipped for it. Only glTF is offered -
a `--format ply` mesh would be read by the PLY parser as a plain cloud and
drawn as its vertices, which is a different picture rather than a cheaper one.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Callable, Optional

from backend.core import colmap, ply

PREVIEW_DIRNAME = "preview"

# The sources the viewer can ask for, in wizard order.
SOURCES: tuple[str, ...] = ("sfm", "train", "mesh")

# What a mesh source reports as its `kind`. `ply.KIND_CLOUD` / `KIND_SPLAT` are
# the two the record formats cover; this one has no record format (see the
# module docstring) and the viewer keys its renderer on the value.
KIND_MESH = "mesh"

# glTF only, and in this order: `GLTFLoader` reads both, and `.glb` is the one
# `spirula mesh` writes with the texture inside it rather than beside it.
_MESH_SUFFIXES = (".glb", ".gltf")

# `sfm auto` writes the sparse cloud here and nowhere else.
_SPARSE_POINTS = "points3D.bin"

_SPLAT_SUFFIXES = (".ply", ".splat")
_STEP_IN_NAME = re.compile(r"step-(\d+)", re.IGNORECASE)

ProgressFn = Callable[[float], None]


class PreviewError(RuntimeError):
    """The preview could not be built - message is meant for the UI."""


# -- Source discovery --------------------------------------------------------

def find_source(project_path: Path, source: str) -> Optional[Path]:
    """The file the given step left behind, or None."""
    if source == "sfm":
        return _find_sparse(project_path)
    if source == "train":
        return _find_splat(project_path)
    if source == "mesh":
        return _find_mesh(project_path)
    raise PreviewError(f"Unknown preview source {source!r}")


def _find_mesh(project_path: Path) -> Optional[Path]:
    """Step 5's glTF mesh, `.glb` before `.gltf`, or None.

    `mesh --output <project>/mesh/mesh` names them, so the glob is over one
    directory and the suffix is what picks. A run asked only for PLY or OBJ
    leaves nothing here on purpose: the viewer says there is nothing to show
    rather than drawing an OBJ's vertices as a point cloud.
    """
    mesh_dir = project_path / "mesh"
    if not mesh_dir.is_dir():
        return None
    for suffix in _MESH_SUFFIXES:
        found = sorted(
            p for p in mesh_dir.glob(f"*{suffix}")
            if p.is_file() and p.stat().st_size > 0
        )
        if found:
            return found[0]
    return None


def _mesh_total(project_path: Path) -> int:
    """Vertices in the mesh, off `mesh_result.json` rather than out of the glb.

    The count is a label under the canvas, not something the viewer needs to
    load the file, and step 5 already read it off the tool's own `stats:` line.
    Parsing a glTF here to re-derive it would be a second, worse reader.
    """
    try:
        report = json.loads(
            (project_path / "mesh" / "mesh_result.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return 0
    value = report.get("vertices")
    return int(value) if isinstance(value, (int, float)) else 0


def _find_sparse(project_path: Path) -> Optional[Path]:
    """`sparse/0/points3D.bin`, through the same finder every model reader uses.

    `colmap.find_model` insists on a *complete* model, so a run killed between
    writing `cameras.bin` and `points3D.bin` reports nothing to preview rather
    than a file the parser would choke on.
    """
    model = colmap.find_model(project_path / "sfm")
    if model is None:
        return None
    points = model / _SPARSE_POINTS
    return points if points.is_file() and points.stat().st_size > 0 else None


def _find_splat(project_path: Path) -> Optional[Path]:
    """The trained splat of the highest checkpoint under `train/`.

    Highest step, not newest mtime: `--save-only-latest-checkpoint` normally
    leaves exactly one `step-%09d.ckpt/`, but a run configured otherwise leaves
    several written minutes apart, and the last one trained is the one worth
    looking at. Files whose name carries no step number fall back to their
    mtime, below everything numbered.
    """
    train = project_path / "train"
    if not train.is_dir():
        return None
    candidates = [
        f for f in train.rglob("*")
        if f.is_file() and f.suffix.lower() in _SPLAT_SUFFIXES
        and f.stat().st_size > 0
    ]
    if not candidates:
        return None

    def rank(path: Path) -> tuple[int, int, float]:
        match = _STEP_IN_NAME.search(str(path))
        step = int(match.group(1)) if match else -1
        # .ply before .splat at the same step: only the PLY carries the
        # spherical harmonics, and it is the file SuperSplat would want.
        return (step, 1 if path.suffix.lower() == ".ply" else 0,
                path.stat().st_mtime)

    return max(candidates, key=rank)


# -- What a source is, and how it converts -----------------------------------

def describe(src: Path) -> dict:
    """Kind and point count of a source, from its header alone."""
    if src.name == _SPARSE_POINTS:
        return {"kind": ply.KIND_CLOUD, "total": colmap.point_count(src)}
    return ply.describe(src)


def _convert(src: Path, dst: Path, max_count: Optional[int],
             progress: Optional[ProgressFn]) -> dict:
    if src.name == _SPARSE_POINTS:
        return ply.write_cloud(
            colmap.iter_points(src), colmap.point_count(src), dst, max_count, progress,
        )
    return ply.convert(src, dst, max_count, progress)


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
        # Relative to the project rather than to a table of directories: the
        # sparse cloud lives under `sfm/sparse/<N>/` and which N it came from
        # is part of the answer.
        "source_url": f"/static/{slug}/{src.relative_to(project_path).as_posix()}",
        "max_count": max_count,
        "ready": False,
    }

    if source == "mesh":
        # The one source with nothing to build: `GLTFLoader` reads the file the
        # tool wrote, so the "preview" URL *is* the source URL and none of the
        # fingerprint / stamp / prune bookkeeping below applies.
        total = _mesh_total(project_path)
        base.update(
            kind=KIND_MESH, total=total, ready=True, url=base["source_url"],
            count=total, bytes=stat.st_size, decimated=False,
        )
        return base

    try:
        described = describe(src)
    except (ply.PlyError, OSError, ValueError) as exc:
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
    if source == "mesh":
        # Nothing to convert - `status` already reports it ready.
        return status(project_path, slug, source, max_count)

    described = describe(src)
    target = _target(project_path, source, described["kind"], max_count,
                     _fingerprint(src))
    if _is_fresh(target, src):
        return status(project_path, slug, source, max_count)

    try:
        meta = _convert(src, target, max_count, progress)
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
