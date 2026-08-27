"""
project_ops.py — Project-level file operations: copy, reset, archive.

Pure-ish by CLAUDE.md §2.4: no FastAPI import, `broadcast_fn` is injected, so
the same functions are callable from a test without a running server.

Everything here works on `projects/<slug>/`, which is user data (§3): nothing in
this module ever touches a directory it was not handed, and **`input/` is never
deleted by a reset** — re-importing the source video is the one thing a reset
must not cost.
"""

import shutil
import zipfile
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Optional

# Directories created for every project (mirrors create_project).
PROJECT_SUBDIRS = (
    "input",
    "frames",
    "masks",
    "analysis",
    "report",
    "rc_output",
    "lfs_output",
    "export",
    # The Reconstruction Region (SESSION 12). Created for every project and
    # absent from STEP_ARTEFACTS below on purpose: a box the user placed by hand
    # is *input* to the mask route, not an artefact of the alignment, so a
    # re-align must not take it. It is the only directory outside `input/` with
    # that property, which is why it is not inside `rc_output/`.
    "region",
)

# What each wizard step leaves on disk, as (directories, individual files).
# Step 1 (import) owns `input/` and is deliberately absent: a reset keeps the
# source video. `region/` is absent for the same class of reason - see
# PROJECT_SUBDIRS above. Only `region_auto.rsbox` in it is derived, and step 3
# overwrites it on every run anyway. Steps 5 and 6 share `export/` — 5 fills it, 6 adds the Blender
# scene to it, so resetting 5 necessarily invalidates 6 as well.
STEP_ARTEFACTS: dict[int, tuple[tuple[str, ...], tuple[str, ...]]] = {
    2: (("frames", "masks", "analysis", "report"), ()),
    3: (("rc_output",), ()),
    4: (("lfs_output",), ()),
    5: (("export",), ()),
    6: ((), ("export/scene.blend", "export/README_SPLATFORGE.txt")),
}

RESETTABLE_STEPS = tuple(sorted(STEP_ARTEFACTS))

# Regenerated from the step outputs on demand (§7.3) — never worth copying or
# archiving, and always worth dropping when its source is reset.
CACHE_DIRS = ("preview",)

BroadcastFn = Callable[..., Awaitable[None]]


async def _say(broadcast_fn: Optional[BroadcastFn], step: str, level: str, msg: str, **kw) -> None:
    if broadcast_fn is not None:
        await broadcast_fn(step, level, msg, **kw)


def _clear_dir(path: Path) -> bool:
    """Empty a directory but keep it — the steps expect their folder to exist."""
    if not path.exists():
        return False
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return True


def ensure_subdirs(project_path: Path) -> None:
    for sub in PROJECT_SUBDIRS:
        (project_path / sub).mkdir(parents=True, exist_ok=True)


def reset_steps(project_path: Path, steps: Iterable[int]) -> list[str]:
    """Delete the artefacts of `steps`. Returns what was actually removed.

    `input/` is untouched by design; the preview cache goes as soon as any step
    that feeds it is reset, since it would otherwise show the previous run's
    cloud next to an empty output directory.
    """
    steps = sorted({int(s) for s in steps if int(s) in STEP_ARTEFACTS})
    removed: list[str] = []

    for step in steps:
        dirs, files = STEP_ARTEFACTS[step]
        for name in dirs:
            if _clear_dir(project_path / name):
                removed.append(f"{name}/")
        for rel in files:
            target = project_path / rel
            if target.exists():
                target.unlink()
                removed.append(rel)

    if any(step >= 3 for step in steps):
        for name in CACHE_DIRS:
            cache = project_path / name
            if cache.exists():
                shutil.rmtree(cache, ignore_errors=True)
                removed.append(f"{name}/ (cache)")

    ensure_subdirs(project_path)
    return removed


def _iter_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


def _payload(root: Path) -> list[Path]:
    """Every file of a project except the regenerable caches."""
    return [
        f for f in _iter_files(root)
        if not any(part in CACHE_DIRS for part in f.relative_to(root).parts)
    ]


# A big file takes long enough that its own line is worth sending; small ones
# are batched, or a 3 000-frame project would put 3 000 messages on the bus.
_PROGRESS_EVERY = 20
_BIG_FILE = 8 * 1024 * 1024


def _report(progress_fn, index: int, total: int, done_bytes: int, size: int) -> None:
    if progress_fn is None:
        return
    if index % _PROGRESS_EVERY == 0 or index == total or size >= _BIG_FILE:
        progress_fn(index, total, done_bytes)


def copy_project_files(src: Path, dst: Path, progress_fn=None) -> tuple[int, int]:
    """Duplicate a project directory, minus the regenerable caches.

    Copies file by file rather than through `copytree` so the caller can report
    progress: a copy is gigabytes and the UI holds a modal open for its whole
    length — a progress bar that only moves at the end is a frozen app.

    Returns (file count, bytes copied).
    """
    ensure_subdirs(dst)
    if not src.exists():
        # A project whose directory was removed by hand still copies — as an
        # empty skeleton — rather than failing the whole operation.
        return 0, 0

    files = _payload(src)
    total = len(files) or 1
    done_bytes = 0

    for index, file in enumerate(files, start=1):
        relative = file.relative_to(src)
        target = dst / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        size = file.stat().st_size
        shutil.copy2(file, target)
        done_bytes += size
        _report(progress_fn, index, total, done_bytes, size)

    ensure_subdirs(dst)
    return len(files), done_bytes


def archive_to_zip(project_path: Path, zip_path: Path, progress_fn=None) -> int:
    """Zip a project directory. Returns the archive size in bytes.

    compresslevel=1 on purpose: a project is mostly PLY — 142 MB of ASCII cloud
    and up to 1.24 GB of gaussians (§7.3) — where level 1 already gets most of
    the ratio for a fraction of the time. An archive nobody waits for is an
    archive nobody makes.
    """
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    files = _payload(project_path)
    total = len(files) or 1
    done_bytes = 0

    tmp = zip_path.with_suffix(zip_path.suffix + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        for index, file in enumerate(files, start=1):
            size = file.stat().st_size
            zf.write(file, file.relative_to(project_path).as_posix())
            done_bytes += size
            _report(progress_fn, index, total, done_bytes, size)
    tmp.replace(zip_path)
    return zip_path.stat().st_size


def restore_from_zip(zip_path: Path, project_path: Path, progress_fn=None) -> tuple[int, int]:
    """Unpack an archive back into place. The zip is left to the caller."""
    project_path.mkdir(parents=True, exist_ok=True)
    root = project_path.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        members = zf.infolist()
        for member in members:
            # Never let a crafted entry write outside the project directory.
            target = (project_path / member.filename).resolve()
            if not str(target).startswith(str(root)):
                raise ValueError(f"Archive entry escapes the project: {member.filename}")

        total = len(members) or 1
        done_bytes = 0
        for index, member in enumerate(members, start=1):
            zf.extract(member, project_path)
            done_bytes += member.file_size
            _report(progress_fn, index, total, done_bytes, member.file_size)

    ensure_subdirs(project_path)
    return len(members), done_bytes
