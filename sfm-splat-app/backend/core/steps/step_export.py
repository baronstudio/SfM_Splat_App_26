"""step_export.py — the second half of step 5: fill `export/`.

Steps 5 and 6 share `export/` (CLAUDE.md §7.10, §14.1): 5 fills it, 6 adds the
Blender scene to it, and resetting 5 therefore takes 6 with it. This module is
the filling.

**Two sources, and neither of them is `lfs_output/`.** That directory went with
LichtFeld Studio (§12, 2026-08-27) and this module used to scan it; it now takes
step 4's `train/run/step-*.ckpt/splat.ply` and step 5's `mesh/` outputs, both
located by the same finders their own steps use rather than by a glob that could
drift from them.

**The files are hard-linked, not copied**, falling back to a copy where the
filesystem refuses. This is `step_conform._link_or_copy`'s argument one step
later: the reference splat is **178 MB** and a textured glb was 30 MB, and
`export/` holds byte-identical copies of files that already exist under
`train/` and `mesh/`. It is safe against every operation the app performs — a
step 5 reset deletes `export/` and `mesh/` and leaves `train/` holding the
splat; a step 4 reset deletes `train/` and leaves `export/` holding the bytes;
nothing in the app ever writes *into* an exported file.

**It does not reset step 5.** `pipeline_runner._run_mesh_and_export` calls this
straight after `run_mesh`, which already cleared `mesh/` and `export/` before it
wrote a byte (§14.1) — a second reset here would delete the mesh it is being
asked to export.

Pure module: no FastAPI import (§2.4).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Optional

from backend.core.steps.step_mesh import find_outputs
from backend.core.steps.step_train import find_splat


def _link_or_copy(source: Path, target: Path) -> None:
    """Hard-link the artefact into `export/`, or copy it if the FS will not."""
    target.unlink(missing_ok=True)
    try:
        os.link(source, target)
    except (OSError, NotImplementedError, AttributeError):
        shutil.copy2(str(source), str(target))


def collect_sources(project_path: Path) -> list[tuple[str, Path]]:
    """`(role, path)` for everything step 5 exports, in wizard order.

    The role is what step 6 and the UI ask by: `splat` is the one file the
    Blender importer wants, and there is exactly one of it.
    """
    found: list[tuple[str, Path]] = []
    splat = find_splat(project_path / "train")
    if splat is not None:
        found.append(("splat", splat))
    found += [("mesh", p) for p in find_outputs(project_path / "mesh")]
    return found


def find_export_splat(export_dir: Path) -> Optional[Path]:
    """The exported splat, told apart from an exported *mesh* PLY.

    `--format ply` writes `mesh.ply`, which sorts before `splat.ply`: the
    predecessor's `glob("*.ply")[0]` would hand step 6 the mesh and Blender
    would import a surface as a gaussian cloud. The name this module wrote is
    what settles it, with the glob left as the fallback for an `export/` filled
    by an older build.
    """
    named = export_dir / "splat.ply"
    if named.is_file():
        return named
    candidates = [
        p for p in sorted(export_dir.glob("*.ply"))
        if not p.stem.startswith("mesh")
    ]
    return candidates[0] if candidates else None


async def run_export(project_path: Path, broadcast_fn, settings: dict) -> dict:
    """Put step 4's splat and step 5's mesh files into `export/`."""
    export_dir = project_path / "export"

    sources = collect_sources(project_path)
    if not sources:
        raise FileNotFoundError(
            f"Nothing to export: no splat under {project_path / 'train'} and no "
            f"mesh under {project_path / 'mesh'}."
        )

    await broadcast_fn(
        "export", "INFO",
        f"[export] {len(sources)} file(s) → export/",
        progress=0.0,
    )

    export_dir.mkdir(parents=True, exist_ok=True)

    files: list[dict[str, Any]] = []
    splat_path: Optional[str] = None
    for done, (role, source) in enumerate(sources, start=1):
        target = export_dir / source.name
        _link_or_copy(source, target)
        if role == "splat":
            splat_path = str(target.resolve())
        files.append({
            "role": role,
            "filename": target.name,
            "bytes": target.stat().st_size,
            "path": str(target.relative_to(project_path)),
        })
        await broadcast_fn(
            "export", "INFO",
            f"[export] {source.relative_to(project_path)} → export/{target.name}",
            progress=done / len(sources),
            file=str(target.resolve()),
        )

    await broadcast_fn(
        "export", "SUCCESS",
        f"[export] {len(files)} file(s) in {export_dir}.",
        progress=1.0,
    )
    return {
        "export_dir": str(export_dir),
        "files": files,
        # Step 6 imports this one; `mesh.ply` is not it.
        "splat_path": splat_path,
    }
