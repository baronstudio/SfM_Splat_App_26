"""step_crop.py - the volume cut on step 4's splat (CLAUDE.md §7.6b).

Not a wizard step: a re-runnable pass on step 4's viewer, the same shape as
`spirula sam` on step 3 and `spirula geometry` on step 4. It never re-trains,
and it never touches what the trainer wrote - it writes a **second** file,
`train/crop/splat.ply`, and steps 5 and 6 prefer it when it is there.

That "beside, never over" is the whole safety argument of this feature, and it
buys four things at the cost of one duplicated file:

* **A crop is undone by deleting one directory.** There is no inverse operation
  to write and no backup to remember to take.
* **A re-crop starts from the trained splat**, not from the last crop, so
  dragging a volume back out restores what it excluded. Cropping a crop would
  make every edit permanent.
* **The two files cannot disagree about which is which.** `find_splat` looks for
  `step-*.ckpt/splat.ply` and finds only the trained one; `find_crop` looks in
  `train/crop/` and finds only the cropped one; `resolve_splat` is the single
  place that chooses, and both readers use it and say which they got.
* **A step 4 reset takes the crop with it**, because `train/crop/` is inside
  `train/` (§14.1) - which is right, since the file is derived from a splat that
  reset just deleted. The *volumes* live in `settings_json` and survive, so a
  re-train re-crops with one click rather than with the gizmo again.

The pass is pure Python, not a subprocess, so it is the first step in this app
whose abort is only the cooperative flag - there is no process tree to kill.
`core/crop.py` checks it between chunks and raises, and this module translates
that into the `ProcessAborted` every step of the pipeline reports abort with.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

from backend.core import crop
from backend.core.proc import ProcessAborted
from backend.core.steps.step_train import find_splat

CROP_DIR_NAME = "crop"
CROP_SPLAT_NAME = "splat.ply"
CROP_RESULT_NAME = "crop_result.json"


def crop_dir(train_dir: Path) -> Path:
    return train_dir / CROP_DIR_NAME


def find_crop(train_dir: Path) -> Optional[Path]:
    """`train/crop/splat.ply`, or None. Never the trained splat."""
    path = crop_dir(train_dir) / CROP_SPLAT_NAME
    return path if path.is_file() and path.stat().st_size > 0 else None


def read_result(train_dir: Path) -> Optional[dict]:
    """`crop_result.json` of the last run, or None."""
    path = crop_dir(train_dir) / CROP_RESULT_NAME
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def resolve_splat(train_dir: Path) -> tuple[Optional[Path], bool]:
    """The splat steps 5 and 6 should read, and whether it is the cropped one.

    Returns `(path, cropped)`. The crop wins when it exists, and **both callers
    log which file they got**: a mesh built from 715 890 gaussians and a mesh
    built from the 300 000 that survived a crop are the same command line and
    very different results, so the run has to name its own input. Same argument
    as step 4 logging its mask count (§5.2) - the trap there was a run that
    exits 0 having silently ignored what it was pointed at.
    """
    cropped = find_crop(train_dir)
    if cropped is not None:
        return cropped, True
    return find_splat(train_dir), False


def volumes_from_settings(settings: dict) -> list[crop.Volume]:
    """The validated volume stack out of the `crop` section of the settings."""
    section = settings.get("crop") if isinstance(settings, dict) else None
    raw = section.get("volumes") if isinstance(section, dict) else None
    return crop.parse_volumes(raw)


def _clear(train_dir: Path) -> bool:
    """Remove `train/crop/` entirely. True when there was something to remove."""
    target = crop_dir(train_dir)
    if not target.exists():
        return False
    shutil.rmtree(target, ignore_errors=True)
    return True


def _describe(volume: crop.Volume) -> str:
    half = " x ".join(f"{2 * h:.3g}" for h in volume.half)
    return f"{volume.mode} {volume.kind} {half}"


def _check_abort(should_abort) -> None:
    if should_abort and should_abort():
        raise ProcessAborted("crop aborted by user")


async def _cut(
    src: Path, dst: Path, volumes: list[crop.Volume], broadcast_fn, should_abort,
) -> dict:
    """The two passes, one chunk per executor hop.

    `crop.apply_crop` does the same thing in one synchronous call, and this is
    deliberately not that: two sequential passes over a 178 MB memory map hold
    the event loop for as long as they take, which stops the WebSocket, the bar
    and — the part that matters — the abort route. Chunking through the executor
    is how `step_analyze` keeps a long pure-Python phase answerable (§15), and
    the empty message on each tick is the same trick step 5 uses for its camera
    counter: `websocket.broadcast` omits it, so the bar moves and the LiveLog
    stays readable.
    """
    started = time.perf_counter()
    loop = asyncio.get_running_loop()
    source = crop.Source(src)

    try:
        total = source.total
        mask = np.empty(total, dtype=bool)

        for lo in range(0, total, crop.CHUNK):
            _check_abort(should_abort)
            hi = min(lo + crop.CHUNK, total)
            mask[lo:hi] = await loop.run_in_executor(
                None, source.mask_chunk, lo, hi, volumes,
            )
            await broadcast_fn("crop", "INFO", "", progress=0.02 + 0.48 * hi / total)

        kept = crop.check_kept(int(mask.sum()))
        tmp = crop.begin_write(dst)

        try:
            with open(tmp, "wb") as out:
                out.write(source.header_bytes(kept))
                for lo in range(0, total, crop.CHUNK):
                    _check_abort(should_abort)
                    hi = min(lo + crop.CHUNK, total)
                    out.write(await loop.run_in_executor(
                        None, source.pack_chunk, mask, lo, hi,
                    ))
                    await broadcast_fn(
                        "crop", "INFO", "", progress=0.50 + 0.49 * hi / total,
                    )
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    finally:
        source.close()

    crop.finalise_to(tmp, dst)
    return crop.result_of(source.total, kept, started, dst)


async def run_crop(
    project_path: Path, broadcast_fn, settings: dict,
    should_abort=None,
) -> dict:
    """Apply the stored volumes to step 4's splat, into `train/crop/`.

    An empty volume stack is not an error and not a no-op: it means *no crop*,
    so it clears `train/crop/` and hands steps 5 and 6 back the trained splat.
    That is what makes the panel's "Remove all" a real undo rather than a state
    the pipeline can get stuck in.
    """
    train_dir = project_path / "train"
    volumes = volumes_from_settings(settings)

    if not volumes:
        removed = _clear(train_dir)
        await broadcast_fn(
            "crop", "INFO",
            "[crop] no volumes - the crop is cleared, steps 5 and 6 read the "
            "trained splat again" if removed
            else "[crop] no volumes and no crop on disk - nothing to do",
            progress=1.0,
        )
        return {"cleared": True, "kept": None}

    source = find_splat(train_dir)
    if source is None:
        raise FileNotFoundError(
            f"No trained splat to crop: nothing matching step-*.ckpt/splat.ply "
            f"under {train_dir}. Run step 4 first."
        )

    target = crop_dir(train_dir) / CROP_SPLAT_NAME
    await broadcast_fn(
        "crop", "INFO",
        f"[crop] {len(volumes)} volume(s) over {source.parent.name}/{source.name}: "
        + ", ".join(_describe(v) for v in volumes),
        progress=0.02,
    )

    result = await _cut(source, target, volumes, broadcast_fn, should_abort)

    percent = 100.0 * result["removed"] / max(result["source_count"], 1)
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source.relative_to(project_path)),
        "output": str(target.relative_to(project_path)),
        "volumes": [v.as_dict() for v in volumes],
        **result,
    }
    (crop_dir(train_dir) / CROP_RESULT_NAME).write_text(
        json.dumps(report, indent=2), encoding="utf-8",
    )

    await broadcast_fn(
        "crop", "SUCCESS",
        f"[crop] {result['kept']:,} of {result['source_count']:,} gaussians kept "
        f"- {result['removed']:,} removed ({percent:.1f}%) in {result['seconds']}s. "
        f"Steps 5 and 6 will read train/crop/splat.ply.",
        progress=1.0,
    )
    return report
