"""step_train.py — step 4: `spirula train`.

One command trains the splat (CLAUDE.md §7.6):

    spirula --lang en train <preset> --data <project>/sfm --image-dir <project>/frames
            --output-dir-prefix <project>/train --output-dir-name run --disable-viewer 1

and writes `train/run/step-%09d.ckpt/splat.ply` beside a flat `config.json` of
every resolved flag. `--data <project>/sfm` finds `sfm/sparse/0` through the
tool's own probe order, and `--image-dir` takes the absolute path of `frames/`,
which is what makes a second copy of the images unnecessary (§5.2).

Four things here are not the obvious implementation, and each of them is a
measurement rather than a preference:

* **`--disable-viewer 1` is not optional.** `keep_viewer_alive` defaults 1 and
  `disable_viewer` defaults 0, so a *successful* run prints `Training complete.
  Viewer still running -- press Ctrl-C to exit.` and parks forever. A
  1-iteration run was still alive when a 90 s timeout fired. The flag is emitted
  here on every run and is not a setting.

* **`--output-dir-name` is not optional either.** With none the build timestamps
  the run directory, and a step that cannot name its own output cannot find it
  again (§12, 2026-08-27).

* **A knob still at the *preset's* default is not sent.** The preset is the
  first positional argument and it moves the defaults of everything under it —
  `meshing` alone moves `--primitive`, `--sh-degree` and `--background-mode` —
  so naming a flag that happens to equal `3dgs`'s value would silently undo the
  preset that was selected. `_PRESET_DEFAULTS` is one row per preset, read off
  `docs/spirula/train-help-all-<preset>.txt`.

* **`--apply-loss-for-mask 1`, and only under the masked route.** The flag
  defaults to 0 and 0 means *ignore*, which `3DGS_App_26` measured as
  indistinguishable from no masks at all (§7.6). The masked route sends 1; a run
  with no masks sends neither, and refuses them outright with `--load-masks 0`.

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
from backend.core import ply
from backend.core.defaults import TrainDefaults, load_defaults
from backend.core.proc import ProcessAborted, iter_lines, release, spawn
from backend.core.project_ops import reset_steps
from backend.core.steps import spirula

# ── The progress channel (§7.7) ──────────────────────────────────────────────
#
# The one tool in this family that gets it right: one CRLF-terminated line every
# 100 steps, written live because `Main.cpp` calls `setvbuf(stdout, nullptr,
# _IONBF, 0)` whenever stdout is not a tty. No CR-redrawn bar, no 4 KB stall.
#
#   step   1101/3000 ( 36%)  splats 58963  [elapsed 0:17 | ETA 0:26]  \
#       rgb_loss=0.09123  ssim=0.744  psnr=21.37
#
_STEP_LINE = re.compile(r"^\s*step\s+(\d+)\s*/\s*(\d+)", re.I)
_SPLATS = re.compile(r"\bsplats\s+(\d+)", re.I)
_ELAPSED = re.compile(r"\belapsed\s+([\d:]+)", re.I)
_ETA = re.compile(r"\bETA\s+([\d:]+)", re.I)
# The values are NOT zero-padded: `psnr=20` and `ssim=0` both occur, so a
# `\d+\.\d+` pattern would drop exactly the lines a run starts and ends on.
_METRIC = re.compile(
    r"\b(rgb_loss|ssim|psnr|loss)\s*=\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)", re.I
)
# `Training complete. Steps: 3000   Time: 0:43` — with --disable-viewer 1 the
# process then exits 0. Without it, this same line is followed by
# `Viewer still running -- press Ctrl-C to exit.` and nothing else, ever.
_COMPLETE = re.compile(
    r"Training complete\.\s*Steps:\s*(\d+)\s+Time:\s*([\d:]+)", re.I
)

# The clock is `M:SS`, not the `01m:31s` shape a first reading might assume.
_CLOCK = re.compile(r"^\d+(?::\d{2})*$")

# `error:` on a line the tool means as a failure. Step 3 needed a lookbehind for
# `Reprojection error:`, the headline number of a *successful* run; the trainer
# has no such counter-example on the lines measured, and the colon is what keeps
# this off the metric names.
_ERROR_LINE = re.compile(r"\berror:", re.I)
_WARNING_LINE = re.compile(r"\bwarn(ing)?\b[: ]", re.I)

# Where the splat lands. `--save-only-latest-checkpoint` defaults 1, so one
# checkpoint normally survives a run — but that is the build's default and not a
# promise, so the highest step is globbed rather than assumed (§7.9).
_STEP_CKPT = re.compile(r"step-(\d+)\.ckpt$", re.I)

RUN_DIR_NAME = "run"

# Map `N/M` onto 5-95 %: a run loads its dataset before step 1 and writes a
# checkpoint after the last, and a bar that sits at 0 through the first and 100
# through the second reports the wrong thing at both ends (§7.7). Capped at 0.99
# while running — the store reads 1.0 as "the step is done".
_P_LOAD, _P_FIRST, _P_LAST, _P_END = 0.02, 0.05, 0.95, 0.99


# ── What each preset sets, read off the installed build ──────────────────────
#
# `docs/spirula/train-help-all-<preset>.txt`, one capture per preset, listing
# only the flags this app models. Everything absent from a row is the same in
# every preset and lives in `_PRESET_BASE`. `train --help` prints six presets;
# `academic-baseline` is a seventh that works and is not listed (§7.6).
_PRESET_BASE: dict[str, Any] = {
    "num_iterations": 30000,
    "quality": "medium",
    "cap_max": 1_000_000,
    "sh_degree": 3,
    "primitive": "3dgs",
    "background_mode": "black",
    "steps_per_save": 2000,
    "save_only_latest_checkpoint": True,
    "save_eval_images": False,
    "distraction_robustness": "off",
    "floater_suppression": "off",
    "load_masks": True,
    # 0 means *ignore*, which is the position that measured as no masks at all.
    "apply_loss_for_mask": False,
    "mask_boundary_offset": 0.0,
    "load_depths": True,
    "load_normals": True,
    "depth_supervision_weight": 0.0,
    "normal_supervision_weight": 0.01,
    "orientation_method": "up",
    "center_method": "poses",
    "auto_scale_poses": True,
    "train_frame": "points",
}

_PRESET_DEFAULTS: dict[str, dict[str, Any]] = {
    "3dgs": {},
    # Distorted 360 images with the lens circle visible: a mip primitive, and
    # the masks pulled in by 2.5 % of the image size to lose the border pixels.
    "360-camera": {"primitive": "mip", "mask_boundary_offset": -0.025},
    "in-the-wild": {
        "distraction_robustness": "strong",
        "center_method": "focus",
        "mask_boundary_offset": -0.025,
    },
    "linear-color": {"background_mode": "noise"},
    "synthetic": {},
    # Aimed at mesh geometry rather than at how the splats look: no
    # view-dependent colour at all, and a noise background so nothing solid is
    # learnt behind the subject.
    "meshing": {"primitive": "3dgut", "sh_degree": 0, "background_mode": "noise"},
    # Unlisted by `--help` and working: measured 2026-08-27, exit 0.
    "academic-baseline": {
        "load_depths": False,
        "load_normals": False,
        "normal_supervision_weight": 0.0,
        "orientation_method": "gsplat",
        "center_method": "gsplat",
    },
}

PRESETS: tuple[str, ...] = tuple(_PRESET_DEFAULTS)


def preset_defaults(preset: str) -> dict[str, Any]:
    """Every modelled flag's default *for this preset*.

    The panel shows these rather than a frozen copy of `3dgs`'s, and
    `_moved_from_preset` diffs against them.
    """
    return {**_PRESET_BASE, **_PRESET_DEFAULTS.get(preset, {})}


def resolve_train_settings(settings: dict) -> TrainDefaults:
    """Overlay the per-project settings onto the app defaults (CLAUDE.md §4).

    Accepts the block nested under `train` or flat, like every other resolver
    here: a run started from the step panel sends it nested, one started from
    elsewhere may not.

    Unlike the other resolvers this one does **not** drop a `None`. Here null is
    a value — "let the preset decide" — so a project that clears a knob the app
    defaults set must be able to say so, and a `v is not None` filter would make
    that the one setting no project can override.
    """
    base = load_defaults().train.model_dump()
    incoming = settings or {}
    nested = incoming.get("train")
    source = nested if isinstance(nested, dict) else incoming
    patch = {k: v for k, v in source.items() if k in base}
    return TrainDefaults.model_validate({**base, **patch})


def _moved_from_preset(
    train: TrainDefaults, overrides: dict[str, Any]
) -> list[tuple[str, Any]]:
    """The knobs whose effective value differs from what the preset would set.

    `None` is not a value to compare, it is the absence of one: it means "leave
    this to the preset", so it never reaches the command line. `overrides` is
    what this run decided regardless of the stored setting — the mask and
    geometry switches, which follow what is actually on disk.
    """
    defaults = preset_defaults(train.preset)
    resolved = {**train.model_dump(), **overrides}
    return [
        (name, resolved[name])
        for name, preset_value in defaults.items()
        if resolved.get(name) is not None and resolved[name] != preset_value
    ]


def resolved_values(train: TrainDefaults) -> dict[str, Any]:
    """What every modelled knob will actually be, preset defaults filled in.

    The command line only carries the differences (`_moved_from_preset`), but a
    log line and a result file have to name the value that was really used —
    "30000 iterations" is what the run did whether or not the flag was sent.
    """
    explicit = {k: v for k, v in train.model_dump().items() if v is not None}
    return {**preset_defaults(train.preset), **explicit}


def _clock_seconds(text: str) -> Optional[float]:
    """`0:43`, `4:20` or `1:02:07` in seconds. None for anything else."""
    if not _CLOCK.match(text):
        return None
    total = 0.0
    for part in text.split(":"):
        total = total * 60 + int(part)
    return total


def _has_files(path: Path) -> bool:
    return path.is_dir() and any(p.is_file() for p in path.iterdir())


def build_command(
    dataset_dir: Path,
    frames_dir: Path,
    train_dir: Path,
    train: TrainDefaults,
    overrides: dict[str, Any],
    masks_dir: Optional[Path],
) -> list[str]:
    """The full `spirula train` command line.

    `--lang en` comes from `spirula.base_command`, not from here (§7.0.1).
    """
    cmd = spirula.base_command("train")
    # The preset is the first positional argument and it moves the defaults of
    # everything under it.
    cmd.append(train.preset)
    cmd += spirula.flag("data", str(dataset_dir))
    # Absolute, and that is the measurement the whole disk layout rests on: a
    # relative value is joined onto --data, an absolute one is used as it is
    # (§5.2). Without it the trainer would look for <project>/sfm/images.
    cmd += spirula.flag("image-dir", str(frames_dir))
    cmd += spirula.flag("output-dir-prefix", str(train_dir))
    cmd += spirula.flag("output-dir-name", RUN_DIR_NAME)
    # Not a setting: without it a *successful* run never returns (§7.6).
    cmd += spirula.flag("disable-viewer", True)

    if masks_dir is not None:
        # --mask-dir is documented in the same words as --image-dir ("Subfolder
        # holding ..."), so an absolute path is expected to work the same way —
        # assumed by symmetry and NOT yet measured (CLAUDE.md §13.4). The run
        # says so in the log, and a wrong path fails loudly here the way it does
        # for --image-dir rather than silently training unmasked.
        cmd += spirula.flag("mask-dir", str(masks_dir))

    cmd += spirula.flags(_moved_from_preset(train, overrides))
    return cmd


def _classify(line: str) -> str:
    if _ERROR_LINE.search(line):
        return "ERROR"
    if _WARNING_LINE.search(line):
        return "WARNING"
    return "INFO"


def _parse_step_line(line: str) -> Optional[dict[str, Any]]:
    """The training bar line, or None if this is not one."""
    match = _STEP_LINE.match(line)
    if not match:
        return None

    out: dict[str, Any] = {
        "iteration": int(match.group(1)),
        "total_iterations": int(match.group(2)),
    }
    splats = _SPLATS.search(line)
    if splats:
        out["num_gaussians"] = int(splats.group(1))
    elapsed = _ELAPSED.search(line)
    if elapsed:
        seconds = _clock_seconds(elapsed.group(1))
        if seconds is not None:
            out["elapsed_s"] = seconds
    eta = _ETA.search(line)
    if eta:
        seconds = _clock_seconds(eta.group(1))
        if seconds is not None:
            # Real, unlike LichtFeld Studio's: derived from the actual step rate
            # and sensible from step 1 onwards.
            out["eta_s"] = seconds

    for name, value in _METRIC.findall(line):
        key = name.lower()
        # The chart speaks one name for the image loss whichever the build
        # prints, so a preset that renames it does not empty the series.
        out["loss" if key in ("rgb_loss", "loss") else key] = float(value)
    return out


def _progress_of(point: dict[str, Any]) -> Optional[float]:
    total = point.get("total_iterations") or 0
    if total <= 0:
        return None
    share = min(point["iteration"] / total, 1.0)
    return min(_P_FIRST + (_P_LAST - _P_FIRST) * share, _P_END)


def _splat_count(splat: Optional[Path]) -> Optional[int]:
    """Gaussians in the written PLY, or None if it cannot be read.

    Only the header is touched, so this costs nothing on a 170 MB file. It is
    never allowed to fail the step: the number is for the report, and a run
    that trained is not a run that failed.
    """
    if splat is None:
        return None
    try:
        return ply.read_header(splat).count
    except Exception:
        return None


def find_splat(train_dir: Path) -> Optional[Path]:
    """`train/run/step-%09d.ckpt/splat.ply` of the highest step, or None.

    Globbed and ranked rather than assumed: `--save-only-latest-checkpoint`
    defaulting to 1 leaves exactly one checkpoint behind, but that is the
    build's default and a run configured otherwise leaves several.
    """
    root = train_dir / RUN_DIR_NAME
    if not root.is_dir():
        root = train_dir
    if not root.is_dir():
        return None

    best: Optional[tuple[int, Path]] = None
    for ckpt in root.glob("**/step-*.ckpt"):
        splat = ckpt / "splat.ply"
        if not (splat.is_file() and splat.stat().st_size > 0):
            continue
        match = _STEP_CKPT.search(ckpt.name)
        step = int(match.group(1)) if match else -1
        if best is None or step > best[0]:
            best = (step, splat)
    return best[1] if best else None


async def _clear_previous_run(project_path: Path, broadcast_fn) -> None:
    """Reset step 4 — after the exe and the dataset are located, never before.

    §14.1: locate the tool and the input first, delete second. The predecessor's
    step 2 had this the other way round and a bad tool path deleted the frames
    it was then unable to re-extract.
    """
    removed = reset_steps(project_path, [4])
    if removed:
        await broadcast_fn(
            "train", "INFO",
            f"[train] Cleared the previous run: {', '.join(removed)}",
            progress=0.0,
        )


def _write_result(train_dir: Path, result: dict) -> None:
    train_dir.mkdir(parents=True, exist_ok=True)
    (train_dir / "train_result.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )


async def run_train(project_path: Path, broadcast_fn, settings: dict) -> dict:
    """Step 4: train the splat with `spirula train`."""
    train = resolve_train_settings(settings)

    # The exe first, and it fails with the path it looked for (§2.2). Before any
    # delete, and before anything is written.
    version = spirula.read_version()
    await broadcast_fn("train", "INFO", f"[train] spirula {version}", progress=0.0)

    dataset_dir = project_path / "sfm"
    model = colmap.find_model(dataset_dir)
    if model is None:
        raise FileNotFoundError(
            f"No sparse model under {dataset_dir}. Run step 3 first — "
            "`train --data` probes sparse/0, colmap/sparse/0, sparse, colmap "
            "and the dataset folder itself, and none of them holds one."
        )

    frames_dir = project_path / "frames"
    images = frame_files.list_frames(frames_dir)
    if not images:
        raise FileNotFoundError(
            f"No images to train on in {frames_dir}. Run step 2 first."
        )

    # -- masks: what is on disk decides, not what the panel remembers ---------
    masks_path = frame_files.masks_dir(project_path)
    mask_count = len(frame_files.list_mask_images(masks_path))
    use_masks = train.load_masks and mask_count > 0
    overrides: dict[str, Any] = {"load_masks": use_masks}

    if use_masks:
        # 1 means "train the masked pixels as empty space", which removes the
        # background and leaves the subject. 0 means *ignore*, and ignore
        # measured as indistinguishable from no masks at all across every column
        # of three 13 000-iteration runs (§7.6) — so the off position is not
        # offered under this route and is not read from the settings.
        overrides["apply_loss_for_mask"] = True
        await broadcast_fn(
            "train", "INFO",
            f"[train] {mask_count} mask(s) in masks/ — trained as empty space "
            "(--apply-loss-for-mask 1), which removes the background rather "
            "than merely dropping it from the loss.",
        )
        await broadcast_fn(
            "train", "INFO",
            f"[train] --mask-dir is given the absolute path {masks_path}. That "
            "an absolute value works here is assumed from --image-dir's "
            "identical help text and is not yet measured (TODO.md P4) — if the "
            "run reports no masks, this is the thing to check.",
        )
    else:
        # `--mask-dir` defaults to `masks` *relative to --data*, i.e.
        # <project>/sfm/masks, which this layout never creates. Refusing them
        # outright is the honest command line rather than pointing the trainer
        # at a directory that is not there.
        overrides["apply_loss_for_mask"] = False
        if mask_count and not train.load_masks:
            await broadcast_fn(
                "train", "WARNING",
                f"[train] {mask_count} mask(s) in masks/ are being ignored "
                "(--load-masks 0).",
            )

    # -- geometry supervision: same rule, off the folder ----------------------
    has_depths = _has_files(dataset_dir / "depths")
    has_normals = _has_files(dataset_dir / "normals")
    overrides["load_depths"] = train.load_depths and has_depths
    overrides["load_normals"] = train.load_normals and has_normals
    if has_depths or has_normals:
        # Both directories sit inside `sfm/`, which is `--data`, so `--depth-dir`
        # and `--normal-dir` keep their relative defaults and cost no flag (§7.5).
        found = " and ".join(
            n for n, ok in (("depths", has_depths), ("normals", has_normals)) if ok
        )
        await broadcast_fn(
            "train", "INFO",
            f"[train] Geometry supervision: sfm/{found} found and used.",
        )

    await _clear_previous_run(project_path, broadcast_fn)

    train_dir = project_path / "train"
    train_dir.mkdir(parents=True, exist_ok=True)

    cmd = build_command(
        dataset_dir, frames_dir, train_dir, train, overrides,
        masks_path if use_masks else None,
    )
    resolved = resolved_values(train)
    await broadcast_fn("train", "INFO", f"[train] Running: {' '.join(cmd)}")
    await broadcast_fn(
        "train", "INFO",
        f"[train] preset {train.preset} · {len(images)} images · "
        f"{resolved['num_iterations']} iterations · "
        f"quality {resolved['quality']} · cap {resolved['cap_max']:,} splats",
        progress=_P_LOAD,
    )

    loop = asyncio.get_running_loop()
    proc = spawn(cmd, project_path, cwd=str(project_path))

    tail: list[str] = []
    last_point: dict[str, Any] = {}
    parsed: dict[str, Any] = {}

    try:
        async for line in iter_lines(proc, loop):
            tail.append(line)
            del tail[:-40]

            point = _parse_step_line(line)
            if point is not None:
                last_point = point
                # One message carrying both the metric and the position. The
                # store reads `progress` above its type switch precisely because
                # of lines like this one (§15.2).
                await broadcast_fn(
                    "train", "INFO", line,
                    progress=_progress_of(point), data=point,
                )
                continue

            match = _COMPLETE.search(line)
            if match:
                parsed["steps"] = int(match.group(1))
                seconds = _clock_seconds(match.group(2))
                if seconds is not None:
                    parsed["elapsed_s"] = seconds

            await broadcast_fn("train", _classify(line), line)

        returncode = await loop.run_in_executor(None, proc.wait)
    finally:
        killed = release(project_path, proc)

    if killed:
        raise ProcessAborted("The training was stopped by the user.")

    splat = find_splat(train_dir)
    result: dict[str, Any] = {
        "exit_code": returncode,
        "spirula_version": version,
        "preset": train.preset,
        "images": len(images),
        "iterations_requested": resolved["num_iterations"],
        "quality": resolved["quality"],
        "cap_max": resolved["cap_max"],
        "primitive": resolved["primitive"],
        "sh_degree": resolved["sh_degree"],
        "masks_used": use_masks,
        "mask_count": mask_count,
        "apply_loss_for_mask": bool(overrides.get("apply_loss_for_mask")),
        "depths_used": overrides["load_depths"],
        "normals_used": overrides["load_normals"],
        "splat_path": str(splat.relative_to(project_path)) if splat else None,
        "splat_bytes": splat.stat().st_size if splat else None,
        # What actually reached disk, read off the PLY header rather than taken
        # from the last bar line. They are not the same number: the trainer's
        # `splats N` is the live count, and the final prune runs after it.
        # Measured on two 30 000-iteration runs — the bar said 1 000 000 (the
        # cap) while the files held 715 890 and 716 831, ~28 % fewer. The cap
        # warning still keys off `num_gaussians`, because hitting the cap
        # *during* training is what it is about.
        "splat_count": _splat_count(splat),
        "command": cmd,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        # The last bar line carries the final loss, ssim, psnr and splat count.
        # `eta_s` is dropped: a countdown to a moment that has already passed.
        **{k: v for k, v in last_point.items() if k != "eta_s"},
        **parsed,
    }
    # Written before the exit code is judged: a failed run's numbers are exactly
    # the ones somebody will want to read afterwards.
    _write_result(train_dir, result)

    if returncode != 0:
        raise RuntimeError(
            f"spirula train exited {returncode}.\n"
            "Last output:\n" + "\n".join(tail[-15:])
        )

    if splat is None:
        # Exit 0 with nothing on disk is not a state the tool is documented to
        # reach, so it is worth failing here rather than letting step 5 be the
        # one that discovers it.
        raise RuntimeError(
            f"spirula train exited 0 but wrote no splat.ply under "
            f"{train_dir / RUN_DIR_NAME}. Last output:\n" + "\n".join(tail[-15:])
        )

    steps_done = parsed.get("steps") or last_point.get("iteration")
    summary = " · ".join(
        part for part in (
            f"{steps_done} steps" if steps_done else None,
            f"{last_point['num_gaussians']:,} splats"
            if "num_gaussians" in last_point else None,
            f"psnr {last_point['psnr']:.2f}" if "psnr" in last_point else None,
            f"ssim {last_point['ssim']:.3f}" if "ssim" in last_point else None,
            f"{parsed['elapsed_s']:.0f} s" if "elapsed_s" in parsed else None,
            f"{result['splat_bytes'] / 1024 ** 2:.1f} MB"
            if result["splat_bytes"] else None,
        ) if part
    )
    await broadcast_fn(
        "train", "SUCCESS",
        f"[train] {summary or 'Training finished'}.",
        progress=_P_END,
        data={"train": result},
    )
    return result
