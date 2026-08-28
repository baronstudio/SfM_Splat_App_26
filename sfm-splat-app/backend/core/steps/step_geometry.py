"""step_geometry.py — depth and normal maps with `spirula geometry` (§7.5).

Not a wizard step: a re-runnable pass on step 4's panel that writes
`sfm/normals/` and `sfm/depths/` **inside the dataset folder**, which both
dataset readers find by name and which nothing else rewrites. `train`'s
`--depth-dir` and `--normal-dir` default to `depths` and `normals` relative to
`--data`, so with `--data <project>/sfm` the pairing costs no flag at all.
Separately re-runnable for the same reason `/analyze` is: **the expensive phase
must never be redone to change a threshold**, and re-aligning to change a normal
format is exactly as unacceptable as re-extracting to change a sensitivity.

Four things here were measured on this workstation on 2026-08-28, and the first
one is the open question CLAUDE.md §13.1 said could force a junction into §5's
layout. It did.

* **`geometry` does NOT resolve images outside the dataset folder.** There is no
  `--image-dir` on this tool. Run against `<project>/sfm`, whose images live in
  the sibling `<project>/frames`, it resolved `<project>/sfm\\images\\frame_0001.jpg`,
  answered `can't fopen` and `skipping` for all 238, and finished
  `done: 0 written, 0 already there, in 0s` — **exit 0**. `--image-dir` buying
  §5.2's single copy of the images is a `train` and `sfm` property, not a
  universal one.

* **A directory junction is the fix, and it lives only for the length of the
  run.** `sfm/images` → `frames/` is created before the command and removed in a
  `finally`, so §5's layout on disk is exactly what §5 says it is and no copy,
  archive, reset or preview ever meets it. With it in place the identical
  command wrote **238 normals in 35 s** at `--max-size 512`, 55 MB. A junction
  needs no administrator and no Developer Mode, unlike `os.symlink`.

* **Exit 0 is not success here.** The failing run above exited 0 having written
  nothing, so the step reads `done: N written` and the folder rather than the
  return code, and a run that wrote nothing fails loudly.

* **The channel is per-image and honest**, `N / M images, T ms each, R left`,
  which is the counter the bar rides. The **checkpoint download is the one
  CR-redrawn bar in this tool family** (§15.1) — 419.4 MB of `moge2-vitb-normal.onnx`
  fetched by a `curl` child, whose percentage redraws arrived as 700-odd
  `iter_lines` fragments on the first run. They drive the bar and never reach the
  log, exactly as `step_mesh` handles its camera counter. The `curl` being a
  **child process** is also why abort has to kill the tree (§2.6) — the process
  holding the work is not the one we spawned.

Pure module: no FastAPI import (§2.4).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backend.core import colmap
from backend.core.defaults import GeometryDefaults, load_defaults
from backend.core.proc import ProcessAborted, iter_lines, release, spawn
from backend.core.steps import spirula

# ── The channel (§15) ────────────────────────────────────────────────────────
_DATASET = re.compile(r"^sfm:\s*(\d+)\s*images,\s*(\d+)\s*cameras", re.I)
_IMAGE_COUNTER = re.compile(r"^(\d+)\s*/\s*(\d+)\s*images\b", re.I)
_DONE = re.compile(
    r"^done:\s*(\d+)\s*written,\s*(\d+)\s*already there(?:,\s*in\s*(\d+)\s*s)?", re.I
)
_FETCHING = re.compile(r"^\[\w+\]\s*fetching\s+(\S+)\s*\(([\d.]+)\s*MB\)", re.I)
_SAVED = re.compile(r"^\[\w+\]\s*saved\s+(.+)$", re.I)
_SKIPPING = re.compile(r"^(skipping\b|load_image:\s*cannot read\b)", re.I)

# The `curl` download bar, CR-redrawn: `iter_lines` splits on CR, so every
# redraw arrives as its own fragment of `#`, `=`, `O`, `-`, digits and a
# percentage. 419.4 MB produced several hundred of them against a 500-line
# LiveLog, which is `_EXTRACT_NOISE`'s problem for the third time (§12,
# 2026-08-27). They ride the bar and are never logged.
_CURL_BAR = re.compile(r"^[#=O\s.%\d-]*$")
_CURL_PERCENT = re.compile(r"([\d.]+)%\s*$")

_ERROR_LINE = re.compile(r"\berror:", re.I)
_WARNING_LINE = re.compile(r"\bwarn(ing)?\b[: ]|can't fopen|cannot read", re.I)

# Where the bar sits. The download is its own stretch because on a first run it
# is most of the wall clock — 419.4 MB against 35 s of inference — and on every
# run after it there is none at all, so the inference stretch simply starts
# early rather than the bar jumping.
_P_START = 0.01
_P_FETCH_START, _P_FETCH_END = 0.02, 0.20
_P_WORK_START, _P_END = 0.22, 0.99

# The image directory `geometry` insists on, relative to the dataset. Measured:
# it resolves `<dataset>\images\<name>` and there is no flag that moves it.
DATASET_IMAGE_DIRNAME = "images"


def resolve_geometry_settings(settings: dict) -> GeometryDefaults:
    """Overlay the per-project settings onto the app defaults (CLAUDE.md §4).

    Accepts the block nested under `geometry` or flat, like every other resolver
    here: a run started from the geometry panel sends it nested, one started
    from elsewhere may not.
    """
    base = load_defaults().geometry.model_dump()
    incoming = settings or {}
    nested = incoming.get("geometry")
    source = nested if isinstance(nested, dict) else incoming
    patch = {k: v for k, v in source.items() if k in base and v is not None}
    return GeometryDefaults.model_validate({**base, **patch})


def resolve_model(geometry: GeometryDefaults) -> Optional[str]:
    """What to hand `--model`, or None to let the build fetch its own default.

    `--model <id|file>`: "A known id is fetched and cached; a path to an .onnx
    file is used as it is." So a configured value that names a file on disk is
    passed as that file — which is also exactly what a failed fetch tells the
    user to do — and anything else is passed through as an id. Empty sends no
    flag at all, and the build fetches `moge2-vitb-normal.onnx` (419.4 MB) into
    `%LOCALAPPDATA%\\spirula-studio\\models\\`.
    """
    value = (geometry.model or "").strip()
    if not value:
        return None
    candidate = Path(value)
    if candidate.is_file():
        return str(candidate)
    return value


def build_command(dataset_dir: Path, geometry: GeometryDefaults) -> list[str]:
    """The full `spirula geometry` command line.

    `--lang en` comes from `spirula.base_command`, not from here (§7.0.1).

    `--depth` and `--overwrite` are bare switches — they take no value, and
    handing one a `0` would be read as the next positional argument — so they go
    through `spirula.switch` rather than `flag`.
    """
    cmd = spirula.base_command("geometry") + [str(dataset_dir)]

    model = resolve_model(geometry)
    if model is not None:
        cmd += spirula.flag("model", model)

    cmd += spirula.flag("max-size", geometry.max_size)
    cmd += spirula.flag("normal-format", geometry.normal_format)
    if geometry.normal_format == "jpg":
        cmd += spirula.flag("jpeg-quality", geometry.jpeg_quality)

    # Off in the tool's own default and off in ours: the normals are what a
    # reconstruction usually wants, and depth doubles both the time on disk and
    # the reading a training run does (§7.5).
    cmd += spirula.switch("depth", geometry.depth)
    if geometry.depth:
        cmd += spirula.flag("depth-units", geometry.depth_units)

    # Both left at `auto` by default, and that is the coherent pair: `--ray-depth
    # auto` picks ray depth exactly when the frame was split into pinhole faces,
    # which is the same call the trainer's `--input-depth-is-ray-depth` makes
    # when it is left unset.
    cmd += spirula.flag("ray-depth", geometry.ray_depth)
    cmd += spirula.flag("split", geometry.split)

    # Without it a run continues where the last one stopped, which is what makes
    # an aborted pass cheap to resume — hence the default off.
    cmd += spirula.switch("overwrite", geometry.overwrite)
    return cmd


def _classify(line: str) -> str:
    if _ERROR_LINE.search(line):
        return "ERROR"
    if _WARNING_LINE.search(line):
        return "WARNING"
    return "INFO"


class _ImageJunction:
    """`<dataset>/images` → `frames/`, for the length of one run and no longer.

    `geometry` has no `--image-dir` and resolves `<dataset>\\images\\<name>`
    (measured; see the module docstring), while §5.2's layout deliberately keeps
    the one copy of the frames outside the dataset. A junction reconciles the two
    without a second copy of the images — 226 MB on the reference project, tens
    of gigabytes on a 4K one.

    **It is created here and removed in `__exit__`**, so nothing else in the app
    ever meets it: not `reset_steps`' `rmtree`, not the project copy's
    `copytree`, not the archive's zip, not `colmap.find_model`. §5's layout on
    disk stays exactly what §5 says it is, and the junction is an implementation
    detail of one command rather than a new rule.

    A junction needs neither administrator rights nor Developer Mode, unlike
    `os.symlink` on Windows; POSIX gets a plain symlink, which needs neither
    there.
    """

    def __init__(self, dataset_dir: Path, frames_dir: Path) -> None:
        self.link = dataset_dir / DATASET_IMAGE_DIRNAME
        self.frames_dir = frames_dir
        self.created = False

    @staticmethod
    def _is_link(path: Path) -> bool:
        # `os.path.isjunction` is 3.12+; 3.11 is supported (§3), so fall back to
        # the symlink test rather than assuming the newer name is there.
        is_junction = getattr(os.path, "isjunction", None)
        return bool(is_junction and is_junction(path)) or os.path.islink(path)

    def __enter__(self) -> "_ImageJunction":
        if self.link.exists() or self._is_link(self.link):
            if self._is_link(self.link):
                # Left by a run that died before its `finally` — ours to reuse.
                os.rmdir(self.link)
            else:
                # A real directory of that name is somebody's data, and this
                # class only ever removes links. Refuse rather than delete.
                raise FileExistsError(
                    f"{self.link} already exists as a real directory. "
                    "`spirula geometry` needs that name for a link to "
                    f"{self.frames_dir}; move it aside and run this again."
                )
        if sys.platform == "win32":
            import _winapi

            _winapi.CreateJunction(str(self.frames_dir), str(self.link))
        else:
            os.symlink(str(self.frames_dir), str(self.link),
                       target_is_directory=True)
        self.created = True
        return self

    def __exit__(self, *_exc: Any) -> None:
        if not self.created:
            return
        try:
            # `os.rmdir` on a junction removes the link and leaves the target
            # untouched — verified, because getting this wrong deletes `frames/`.
            os.rmdir(self.link)
        except OSError:
            pass


def _write_result(dataset_dir: Path, result: dict) -> None:
    dataset_dir.mkdir(parents=True, exist_ok=True)
    (dataset_dir / "geometry_result.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )


def _count(directory: Path, suffix: Optional[str] = None) -> int:
    if not directory.is_dir():
        return 0
    return sum(
        1 for p in directory.iterdir()
        if p.is_file() and (suffix is None or p.suffix.lower() == suffix)
    )


def _stale_normals(normals_dir: Path, normal_format: str) -> int:
    """Normal maps left behind in the *other* format.

    Measured 2026-08-28: a `--normal-format png` run followed by a
    `--normal-format jpg` one left `sfm/normals/` holding 476 files for 238
    frames — the tool writes the new format beside the old rather than replacing
    it, because `--overwrite` is about recomputing a map, not about a file whose
    name no longer matches. Which of the two `train --normal-dir` then reads is
    not something this app should be guessing at on the user's behalf, so the
    run says so and names the number.
    """
    other = ".png" if normal_format == "jpg" else ".jpg"
    return _count(normals_dir, other)


async def run_geometry(project_path: Path, broadcast_fn, settings: dict) -> dict:
    """Write `sfm/normals/` (and `sfm/depths/`) with `spirula geometry`."""
    geometry = resolve_geometry_settings(settings)

    version = spirula.read_version()
    await broadcast_fn(
        "geometry", "INFO", f"[geometry] spirula {version}", progress=0.0
    )

    dataset_dir = project_path / "sfm"
    if colmap.find_model(dataset_dir) is None:
        raise FileNotFoundError(
            f"No sparse model under {dataset_dir}. Run step 3 first — the "
            "geometry pass reads the reconstruction's cameras, not the frames "
            "on their own."
        )

    frames_dir = project_path / "frames"
    if not frames_dir.is_dir():
        raise FileNotFoundError(
            f"No frames under {frames_dir}. Run step 2 first — the depth and "
            "normal maps are estimated from the images one at a time."
        )

    # **Never a reset.** These maps live inside `sfm/`, which is step 3's
    # directory in §14.1's table: clearing it here would delete the sparse model
    # the run is about to read. Without `--overwrite` the tool continues where
    # the last run stopped, which is what makes an aborted pass cheap to resume.
    normals_dir, depths_dir = dataset_dir / "normals", dataset_dir / "depths"
    before = {"normals": _count(normals_dir), "depths": _count(depths_dir)}
    if before["normals"] or before["depths"]:
        await broadcast_fn(
            "geometry", "INFO",
            f"[geometry] sfm/ already holds {before['normals']} normal and "
            f"{before['depths']} depth map(s). "
            + ("--overwrite: they are recomputed."
               if geometry.overwrite else
               "This run continues where the last one stopped rather than "
               "redoing them."),
        )

    if resolve_model(geometry) is None:
        await broadcast_fn(
            "geometry", "INFO",
            "[geometry] No checkpoint named, so the build fetches its own on "
            "first use — moge2-vitb-normal.onnx, 419.4 MB, from HuggingFace "
            "into its model cache. The licence of that checkpoint is not "
            "Apache-2.0 by default: see §10. Later runs reuse it.",
        )

    cmd = build_command(dataset_dir, geometry)
    await broadcast_fn("geometry", "INFO", f"[geometry] Running: {' '.join(cmd)}")

    loop = asyncio.get_running_loop()
    tail: list[str] = []
    parsed: dict[str, Any] = {}
    skipped = 0
    progress = _P_START

    # The junction exists only inside this block. `geometry` has no
    # `--image-dir`, so without it the run finds no images at all — and says so
    # while still exiting 0.
    with _ImageJunction(dataset_dir, frames_dir) as junction:
        await broadcast_fn(
            "geometry", "INFO",
            f"[geometry] {junction.link.name}/ linked to frames/ for the length "
            "of this run: `geometry` resolves <dataset>/images/<name> and has no "
            "--image-dir. Removed again when it finishes — there is still only "
            "one copy of the frames on disk (§5.2).",
            progress=_P_START,
        )

        proc = spawn(cmd, project_path, cwd=str(project_path))
        try:
            async for line in iter_lines(proc, loop):
                # The curl bar first: several hundred CR fragments per download,
                # against a 500-line LiveLog. They move the bar and are never
                # logged — `broadcast` omits an empty message from the payload,
                # so the store never records one.
                if _CURL_BAR.match(line):
                    percent = _CURL_PERCENT.search(line)
                    if percent:
                        share = min(float(percent.group(1)) / 100.0, 1.0)
                        progress = max(
                            progress,
                            _P_FETCH_START + (_P_FETCH_END - _P_FETCH_START) * share,
                        )
                        await broadcast_fn("geometry", "INFO", "", progress=progress)
                    continue

                # 476 of the 483 non-bar lines of the failing run were these two,
                # one pair per image. Counted, not logged: the count is the
                # finding and the 476 lines are the same `_EXTRACT_NOISE` trap.
                if _SKIPPING.match(line):
                    skipped += 1
                    continue

                found = _DATASET.match(line)
                if found:
                    parsed["images"] = int(found.group(1))
                    parsed["cameras"] = int(found.group(2))

                found = _FETCHING.match(line)
                if found:
                    parsed["fetched_model"] = found.group(1)
                    parsed["fetched_mb"] = float(found.group(2))
                    progress = max(progress, _P_FETCH_START)

                if _SAVED.match(line):
                    progress = max(progress, _P_FETCH_END)

                found = _IMAGE_COUNTER.match(line)
                if found:
                    done, total = int(found.group(1)), int(found.group(2))
                    if total > 0:
                        progress = max(
                            progress,
                            _P_WORK_START
                            + (_P_END - _P_WORK_START) * min(done / total, 1.0),
                        )

                found = _DONE.match(line)
                if found:
                    parsed["written"] = int(found.group(1))
                    parsed["already_there"] = int(found.group(2))
                    if found.group(3):
                        parsed["elapsed_s"] = int(found.group(3))
                    progress = _P_END

                tail.append(line)
                del tail[:-40]
                await broadcast_fn(
                    "geometry", _classify(line), line, progress=progress
                )

            returncode = await loop.run_in_executor(None, proc.wait)
        finally:
            killed = release(project_path, proc)

    if killed:
        raise ProcessAborted("The geometry pass was stopped by the user.")

    after = {"normals": _count(normals_dir), "depths": _count(depths_dir)}
    result: dict[str, Any] = {
        "exit_code": returncode,
        "spirula_version": version,
        "model": resolve_model(geometry) or "the build's own default",
        "max_size": geometry.max_size,
        "normal_format": geometry.normal_format,
        "depth": geometry.depth,
        "ray_depth": geometry.ray_depth,
        "split": geometry.split,
        "overwrite": geometry.overwrite,
        "skipped_images": skipped,
        "normals": after["normals"],
        "depths": after["depths"],
        "normals_before": before["normals"],
        "depths_before": before["depths"],
        "stale_normals": _stale_normals(normals_dir, geometry.normal_format),
        "command": cmd,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        **parsed,
    }
    # Written before the exit code is judged, like every other step's result.
    _write_result(dataset_dir, result)

    if returncode != 0:
        raise RuntimeError(
            f"spirula geometry exited {returncode}.\n"
            "Last output:\n" + "\n".join(tail[-15:])
        )

    # **Exit 0 is not success here.** The run that could not find its images
    # skipped all 238 and finished `done: 0 written, 0 already there, in 0s`
    # with a zero return code. The folder is the answer, not the exit status.
    if not after["normals"] and not after["depths"]:
        raise RuntimeError(
            f"spirula geometry exited 0 but wrote no maps under {dataset_dir}"
            + (f", skipping {skipped} image(s) it could not read" if skipped else "")
            + ".\nLast output:\n" + "\n".join(tail[-15:])
        )

    if skipped:
        await broadcast_fn(
            "geometry", "WARNING",
            f"[geometry] {skipped} image(s) were skipped because the run could "
            "not read them. The maps that were written are still usable; the "
            "frames without one simply carry no geometry term.",
        )

    stale = _stale_normals(normals_dir, geometry.normal_format)
    if stale:
        await broadcast_fn(
            "geometry", "WARNING",
            f"[geometry] sfm/normals/ still holds {stale} map(s) in the other "
            f"format beside this run's {geometry.normal_format}. The tool writes "
            "the new format next to the old rather than replacing it, and which "
            "of the two `train --normal-dir` reads is not something to guess at "
            "— delete the stale ones, or re-run in the format you want to keep.",
        )

    summary = " · ".join(
        part for part in (
            f"{after['normals']:,} normal maps" if after["normals"] else None,
            f"{after['depths']:,} depth maps" if after["depths"] else None,
            f"{parsed['already_there']:,} already there"
            if parsed.get("already_there") else None,
            f"{parsed['elapsed_s']} s" if "elapsed_s" in parsed else None,
        ) if part
    )
    await broadcast_fn(
        "geometry", "SUCCESS",
        f"[geometry] {summary}. They sit inside sfm/, so step 4 reads them "
        "through --data with no flag. A step 3 re-run deletes them with the "
        "rest of sfm/ (§14.1).",
        progress=_P_END, data={"geometry": result},
    )
    return result
