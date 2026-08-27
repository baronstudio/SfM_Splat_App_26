import shutil
from pathlib import Path

from backend.core.project_ops import reset_steps


async def run_export(project_path: Path, broadcast_fn, settings: dict) -> dict:
    """
    Collects .ply and .splat files from lfs_output/ and copies them to export/.
    Broadcasts a file_ready event for each file found.
    """
    lfs_output = project_path / "lfs_output"
    export_dir = project_path / "export"

    await broadcast_fn(
        "export", "INFO",
        f"Scanning {lfs_output} for output files...",
        progress=0.0,
    )

    ply_files = list(lfs_output.glob("*.ply"))
    splat_files = list(lfs_output.glob("*.splat"))

    if not ply_files and not splat_files:
        raise FileNotFoundError(f"No .ply or .splat files found in {lfs_output}")

    # A re-export is a reset of step 5, like steps 2, 3 and 4 before it. This
    # copies whatever `lfs_output/` holds under its own name, so a training that
    # stopped at a different iteration lands beside the previous splat instead
    # of replacing it - and nothing in `export/` then says which one is current.
    # Now that step 4 clears `lfs_output/`, `export/` was the last place a stale
    # splat could survive a re-run.
    #
    # It takes step 6's `scene.blend` and `README_SPLATFORGE.txt` with it, which
    # is the documented meaning of resetting step 5 (CLAUDE.md 14.1): the two
    # steps share `export/`, and a Blender scene pointing at a splat that is no
    # longer there is not worth keeping. Re-run step 6 after a re-export.
    #
    # After the scan, never before: an empty `lfs_output/` must not cost the
    # export already on disk, the same rule as the exe checks in steps 2-4.
    removed = reset_steps(project_path, [5])
    if removed:
        await broadcast_fn(
            "export", "INFO",
            f"[export] Cleared the previous export ({', '.join(removed)}) - "
            f"re-run step 6 if you need the Blender scene.",
        )

    export_dir.mkdir(exist_ok=True)

    ply_path: str | None = None
    splat_path: str | None = None
    total = len(ply_files) + len(splat_files)
    done = 0

    for ply in ply_files:
        dest = export_dir / ply.name
        shutil.copy2(str(ply), str(dest))
        ply_path = str(dest.resolve())
        done += 1
        await broadcast_fn(
            "export", "INFO",
            f"Copied {ply.name} → export/",
            progress=done / total,
            file=ply_path,
        )

    for splat in splat_files:
        dest = export_dir / splat.name
        shutil.copy2(str(splat), str(dest))
        splat_path = str(dest.resolve())
        done += 1
        await broadcast_fn(
            "export", "INFO",
            f"Copied {splat.name} → export/",
            progress=done / total,
            file=splat_path,
        )

    await broadcast_fn(
        "export", "SUCCESS",
        f"Export complete — {done} file(s) → {export_dir}",
        progress=1.0,
    )
    return {
        "ply_path": ply_path,
        "splat_path": splat_path,
        "export_dir": str(export_dir),
    }
