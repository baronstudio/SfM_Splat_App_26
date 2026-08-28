"""step_sfm.py — step 3: `spirula sfm auto`.

One command reconstructs the sparse model (CLAUDE.md §7.1):

    spirula --lang en sfm auto <project>/frames -o <project>/sfm [--quality Q] ...

and writes `sfm/{features/, matches.bin, sparse/0..N}` beside `frames/` rather
than a second copy of the images — which is what `--image-dir` accepting an
absolute path bought (§5.2). `masks/` is already the sibling `sfm auto` adopts
without being named, so the mask route costs no flag; `--no-masks` is what
refuses it.

Two things here are not the obvious implementation and both are measured:

* **Exit 3 warns and never fails the pipeline** (§7.1). The tool grades its own
  reconstruction in the exit code and 3 means "partial" — under half the images
  registered, or over 2 px mean reprojection. Blocking on that stops a pipeline
  over a handful of unalignable frames, and re-running is the user's call. The
  number is named in the log and persisted to `sfm/sfm_result.json`, because the
  answer to "did this actually work" must outlive the scrollback.

* **A knob still at the build's own default is not sent.** `--quality` and
  `--data-type` are presets: they move `--max-image-size`, `--max-features`,
  `--prefilter-neighbors` and the pair selection, and the run reports what they
  moved. Naming a flag explicitly overrides the preset — so passing
  `--max-features 8192` because that is also the build default would silently
  undo `--quality medium`'s drop to 4096. See `_moved_from_build_default`.

Pure module: no FastAPI import (§2.4).
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backend.core import colmap
from backend.core import frames as frame_files
from backend.core.defaults import SfmDefaults, load_defaults
from backend.core.proc import ProcessAborted, iter_lines, release, spawn
from backend.core.project_ops import reset_steps
from backend.core.steps import spirula

# ── The tagged stdout channel (§7.2) ─────────────────────────────────────────
#
# `sfm auto` prints width-padded stage lines live, and two of them carry a
# denominator. The images are NOT processed in filename order (the reference run
# started 00227, 00165, 00005…), so the counter is the progress and the filename
# beside it is not.
_EXTRACT_N = re.compile(r"^\[extract\]\s+(\d+)\s*/\s*(\d+)")
_MAP_N = re.compile(r"images in the model:\s*(\d+)")

# The extractor narrates its keypoint pyramid three lines deep for every single
# image, and only the fourth line — the `N/total` counter above — names the file
# they were about. Measured on the 300-image reference run: 1682 lines out, of
# which 900 were these. The LiveLog keeps 500, so leaving them in meant the run's
# own `The presets set --max-image-size to 2400 (was 0)` header was pushed out of
# the buffer before the reconstruction was a third of the way through — the four
# lines that say what the run is actually doing, lost to the three that describe
# one image's octaves.
_EXTRACT_NOISE = re.compile(
    r"^\[extract\]\s+(Octaves|Oriented|Features|Raw keypoints|Selected):"
)

# The `[run]` result block, which is what `sfm_result.json` is built from.
_REGISTERED = re.compile(r"Registered:\s*(\d+)\s*/\s*(\d+)\s*images", re.I)
_REPROJECTION = re.compile(
    r"Reprojection error:\s*mean\s*([\d.]+)\s*px,\s*median\s*([\d.]+)\s*px"
    r"(?:,\s*over\s*(\d+)\s*observations)?",
    re.I,
)
_POINTS = re.compile(r"\bPoints:\s*(\d+)")
_CAMERAS = re.compile(r"\bCameras:\s*(\d+)")
_TOTAL_S = re.compile(r"^\[run\]\s+Total:\s*([\d.]+)\s*s", re.I)

# A tool line that is actually a failure. `Reprojection error: mean 0.502 px,
# median 0.392 px` is the counter-example, and it is not hypothetical: it is the
# headline quality number of a *successful* run and it carries the token
# `error:` verbatim, so the obvious pattern paints every good reconstruction red
# in the LiveLog. Hence the lookbehind.
_ERROR_LINE = re.compile(r"(?<!reprojection )\berror:", re.I)
_WARNING_LINE = re.compile(r"\bwarn(ing)?\b[: ]", re.I)

# What each exit code means, in the tool's own words (`sfm auto --help`).
_EXIT_MEANING = {
    0: "a reconstruction that looks sound",
    1: "usage or runtime error",
    2: "no reconstruction at all",
    3: "partial: under half the images registered, or over 2 px mean reprojection",
}

# The values the installed build prints as its own defaults. A knob equal to one
# of these is left off the command line so `--quality` / `--data-type` can still
# move it — see the module docstring. `--quality` and `--data-type` are absent on
# purpose: they *are* the presets and are always sent.
_BUILD_DEFAULTS: dict[str, Any] = {
    "pairs": "auto",
    "camera_model": "opencv",
    "camera_mode": "folder",
    "max_image_size": 0,
    "max_features": 8192,
}

# Where the bar sits when a phase starts. Extraction and mapping are the two
# countable ones; matching prints no denominator, so its share is a floor the
# bar rests on rather than a range it crosses — after 10 s without a message
# ProgressBar switches to indeterminate stripes and keeps this number beside
# them, which is the honest report (§15.3).
_P_START, _P_EXTRACT, _P_MATCH, _P_MAP, _P_END = 0.01, 0.35, 0.38, 0.95, 0.99


def resolve_sfm_settings(settings: dict) -> SfmDefaults:
    """Overlay the per-project settings onto the app defaults (CLAUDE.md §4).

    Accepts the block nested under `sfm` or flat, like every other resolver
    here: a run started from the step panel sends it nested, one started from
    elsewhere may not.
    """
    base = load_defaults().sfm.model_dump()
    incoming = settings or {}
    nested = incoming.get("sfm")
    source = nested if isinstance(nested, dict) else incoming
    patch = {k: v for k, v in source.items() if k in base and v is not None}
    return SfmDefaults.model_validate({**base, **patch})


def _moved_from_build_default(sfm: SfmDefaults) -> list[tuple[str, Any]]:
    """The knobs the user actually moved — the only ones worth naming.

    Everything left out stays at whatever `--quality` and `--data-type` set it
    to, and the run's own `The presets set --max-features to 4096 (was 8192)`
    lines then say what happened.
    """
    return [
        (name, getattr(sfm, name))
        for name, build_value in _BUILD_DEFAULTS.items()
        if getattr(sfm, name) != build_value
    ]


def build_command(
    frames_dir: Path,
    sfm_dir: Path,
    sfm: SfmDefaults,
    use_masks: bool,
) -> list[str]:
    """The full `spirula sfm auto` command line.

    `--lang en` comes from `spirula.base_command`, not from here (§7.0.1).

    The positional is `frames/` itself and not the project directory: `auto`
    accepts a dataset directory holding an `images/` sub-directory and uses that
    instead, and a project has no `images/` — naming the image directory
    outright means the two readings cannot diverge.
    """
    cmd = spirula.base_command("sfm") + ["auto", str(frames_dir), "-o", str(sfm_dir)]

    # The two headline knobs, always sent: they are the interface (§7.1).
    cmd += spirula.flag("quality", sfm.quality)
    cmd += spirula.flag("data-type", sfm.data_type)
    cmd += spirula.flags(_moved_from_build_default(sfm))

    # `sfm auto`'s bools are bare `--no-x` switches whose help prints their
    # *current* state, not a value to hand back (§7.0.4) — hence `switch`, which
    # emits the flag or nothing at all. `--no-masks` is the refusal: adoption is
    # automatic for a `masks/` sibling of the image directory.
    cmd += spirula.switch("no-masks", not use_masks)

    if sfm.progress_dir:
        cmd += spirula.flag("progress-dir", str(sfm_dir / "progress"))
    return cmd


def _classify(line: str) -> str:
    if _ERROR_LINE.search(line):
        return "ERROR"
    if _WARNING_LINE.search(line):
        return "WARNING"
    return "INFO"


def _phase_progress(line: str, total_images: int, state: dict) -> Optional[float]:
    """Where the bar goes for this line, or None if it says nothing about it."""
    match = _EXTRACT_N.match(line)
    if match:
        done, total = int(match.group(1)), int(match.group(2)) or total_images
        state["phase"] = "extract"
        if total <= 0:
            return None
        return _P_START + (_P_EXTRACT - _P_START) * min(done / total, 1.0)

    if line.startswith("[match]") and state.get("phase") != "match":
        state["phase"] = "match"
        # No denominator on this phase: the bar parks here and says so.
        return _P_MATCH

    match = _MAP_N.search(line)
    if match and total_images > 0:
        state["phase"] = "map"
        done = int(match.group(1))
        return _P_MATCH + (_P_MAP - _P_MATCH) * min(done / total_images, 1.0)

    return None


async def _clear_previous_run(project_path: Path, broadcast_fn) -> None:
    """Reset step 3 — after the exe and the frames are located, never before.

    §14.1: locate the tool and the input first, delete second. The predecessor's
    step 2 had this the other way round and a bad tool path deleted the frames it
    was then unable to re-extract.

    `sfm/depths/` and `sfm/normals/` sit inside `sfm/`, so a re-run costs the
    geometry pass too (§7.5). That is correct — they are per-image maps of the
    images *this* reconstruction registered — but it is expensive enough that the
    step says so by name rather than letting the user find out at step 4.
    """
    sfm_dir = project_path / "sfm"
    geometry = [
        name for name in ("depths", "normals")
        if (sfm_dir / name).is_dir() and any((sfm_dir / name).iterdir())
    ]
    if geometry:
        await broadcast_fn(
            "sfm", "WARNING",
            f"[sfm] This re-run also deletes sfm/{', sfm/'.join(geometry)} — "
            "the geometry supervision maps describe the images this "
            "reconstruction registered, so they go with it. Re-run the geometry "
            "panel on step 4 afterwards.",
        )

    removed = reset_steps(project_path, [3])
    if removed:
        await broadcast_fn(
            "sfm", "INFO", f"[sfm] Cleared the previous run: {', '.join(removed)}",
            progress=0.0,
        )


def _write_result(sfm_dir: Path, result: dict) -> None:
    sfm_dir.mkdir(parents=True, exist_ok=True)
    (sfm_dir / "sfm_result.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )


async def run_sfm(project_path: Path, broadcast_fn, settings: dict) -> dict:
    """Step 3: reconstruct the sparse model with `spirula sfm auto`."""
    sfm = resolve_sfm_settings(settings)

    # The exe first, and it fails with the path it looked for (§2.2). Before any
    # delete, and before anything is written.
    version = spirula.read_version()
    await broadcast_fn("sfm", "INFO", f"[sfm] spirula {version}", progress=0.0)

    frames_dir = project_path / "frames"
    images = frame_files.list_frames(frames_dir)
    if not images:
        raise FileNotFoundError(
            f"No frames to reconstruct in {frames_dir}. Run step 2 first."
        )
    total_images = len(images)

    masks = frame_files.masks_dir(project_path)
    mask_count = len(frame_files.list_mask_images(masks))
    # `sfm auto` adopts a `masks/` sibling of the image directory by itself
    # (§5.2), so `use_masks` is only ever a refusal. An empty directory is made
    # an explicit refusal rather than left to whatever the tool does with a
    # directory holding nothing — that behaviour is unmeasured, and a run that
    # died on it would die after the extraction phase.
    use_masks = sfm.use_masks and mask_count > 0
    if sfm.use_masks and mask_count == 0:
        await broadcast_fn(
            "sfm", "INFO",
            "[sfm] masks/ is empty — reconstructing on the full frames.",
        )
    elif use_masks:
        await broadcast_fn(
            "sfm", "INFO",
            f"[sfm] {mask_count} mask(s) in masks/ — keypoints on black pixels "
            "are dropped. The pairing is by the frame's own basename; if the "
            "run reports no masks, that convention is the thing to check "
            "(TODO.md P4).",
        )
    elif not sfm.use_masks and mask_count:
        await broadcast_fn(
            "sfm", "WARNING",
            f"[sfm] {mask_count} mask(s) in masks/ are being ignored (--no-masks).",
        )

    await _clear_previous_run(project_path, broadcast_fn)

    sfm_dir = project_path / "sfm"
    sfm_dir.mkdir(parents=True, exist_ok=True)

    cmd = build_command(frames_dir, sfm_dir, sfm, use_masks)
    await broadcast_fn("sfm", "INFO", f"[sfm] Running: {' '.join(cmd)}")
    await broadcast_fn(
        "sfm", "INFO",
        f"[sfm] {total_images} images · quality {sfm.quality} · "
        f"data type {sfm.data_type}",
        progress=_P_START,
    )

    loop = asyncio.get_running_loop()
    proc = spawn(cmd, project_path, cwd=str(project_path))

    tail: list[str] = []
    parsed: dict[str, Any] = {}
    state: dict[str, Any] = {"phase": "start"}

    try:
        async for line in iter_lines(proc, loop):
            if _EXTRACT_NOISE.match(line):
                continue

            tail.append(line)
            del tail[:-40]

            progress = _phase_progress(line, total_images, state)
            await broadcast_fn("sfm", _classify(line), line, progress=progress)

            match = _REGISTERED.search(line)
            if match:
                parsed["registered"] = int(match.group(1))
                parsed["total"] = int(match.group(2))
            match = _REPROJECTION.search(line)
            if match:
                parsed["reprojection_mean_px"] = float(match.group(1))
                parsed["reprojection_median_px"] = float(match.group(2))
                if match.group(3):
                    parsed["observations"] = int(match.group(3))
            if line.startswith("[run]"):
                match = _POINTS.search(line)
                if match:
                    parsed["points"] = int(match.group(1))
                match = _CAMERAS.search(line)
                if match:
                    parsed["camera_groups"] = int(match.group(1))
                match = _TOTAL_S.search(line)
                if match:
                    parsed["elapsed_s"] = float(match.group(1))

        returncode = await loop.run_in_executor(None, proc.wait)
    finally:
        killed = release(project_path, proc)

    if killed:
        raise ProcessAborted("The reconstruction was stopped by the user.")

    sparse_models = colmap.count_models(sfm_dir)
    result: dict[str, Any] = {
        "exit_code": returncode,
        "exit_meaning": _EXIT_MEANING.get(returncode, "unknown exit code"),
        "spirula_version": version,
        "images": total_images,
        "sparse_models": sparse_models,
        "masks_used": use_masks,
        "mask_count": mask_count,
        "quality": sfm.quality,
        "data_type": sfm.data_type,
        "camera_model": sfm.camera_model,
        "command": cmd,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        **parsed,
    }
    # Written before the exit code is judged: a failed run's numbers are exactly
    # the ones somebody will want to read afterwards.
    _write_result(sfm_dir, result)

    if returncode not in _EXIT_MEANING or returncode in (1, 2):
        raise RuntimeError(
            f"spirula sfm auto exited {returncode} "
            f"({_EXIT_MEANING.get(returncode, 'unknown exit code')}).\n"
            "Last output:\n" + "\n".join(tail[-15:])
        )

    if colmap.find_model(sfm_dir) is None:
        # Exit 0 with nothing on disk is not a state the tool is documented to
        # reach, so it is worth failing loudly rather than letting step 4 be the
        # one that discovers it.
        raise RuntimeError(
            f"spirula sfm auto exited {returncode} but wrote no sparse model "
            f"under {sfm_dir}. Last output:\n" + "\n".join(tail[-15:])
        )

    registered = parsed.get("registered")
    total = parsed.get("total", total_images)
    mean_px = parsed.get("reprojection_mean_px")

    if returncode == 3:
        # Never a failure (§7.1, §12): the decision to re-run is the user's, and
        # a partial reconstruction is often still the one they want to train on.
        await broadcast_fn(
            "sfm", "WARNING",
            "[sfm] Exit 3 — partial reconstruction: "
            + (f"{registered}/{total} images registered" if registered is not None
               else "under half the images registered")
            + (f", {mean_px:.2f} px mean reprojection" if mean_px is not None else "")
            + ". The pipeline continues; re-run with --data-type video or a "
              "higher --quality if step 4 disappoints.",
        )

    if sparse_models > 1:
        # `sparse/N` with N > 0 is what splits the output — camera *groups* are
        # not components, and a `Cameras: 2` from one folder is two intrinsics
        # (§7.1).
        await broadcast_fn(
            "sfm", "WARNING",
            f"[sfm] {sparse_models} sparse models — this capture is not one "
            "connected view graph. sparse/0 is the largest component and the "
            "only one step 4 trains on. Raise --overlap or switch --data-type "
            "to video and re-run if that loses too much of the scene.",
        )

    summary = " · ".join(
        part for part in (
            f"{registered}/{total} images registered" if registered is not None else None,
            f"{mean_px:.2f} px mean reprojection" if mean_px is not None else None,
            f"{parsed['points']} points" if "points" in parsed else None,
            f"{parsed['camera_groups']} camera group(s)" if "camera_groups" in parsed else None,
            f"{parsed['elapsed_s']:.1f} s" if "elapsed_s" in parsed else None,
        ) if part
    )
    await broadcast_fn(
        "sfm", "SUCCESS" if returncode == 0 else "WARNING",
        f"[sfm] {summary or 'Reconstruction finished'}.",
        progress=_P_END,
        data={"sfm": result},
    )
    return result
