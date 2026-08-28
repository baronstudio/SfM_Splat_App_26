"""step_sam.py — masking with `spirula sam` (CLAUDE.md §7.4).

Not a wizard step: a re-runnable pass that writes `projects/<slug>/masks/`,
which both later tools adopt as a sibling of the image directory with no flag at
all (§5.2). It is modelled line for line on `/analyze`, for the same reason —
**the expensive phase must never be redone to change a threshold.** Re-aligning
to change a lens border is exactly as unacceptable as re-extracting to change a
sharpness sensitivity.

`sam` has six subcommands; two of them are here, and they are one setting with a
mode rather than two features because their costs differ by everything:

* **`shape` → `sam mask`.** No model, no download, no licence question. It masks
  the part of every frame that is never scene — a fisheye border, a watermark,
  the rig in shot — "so it is a shape, not an object". This is the companion of
  §1's 360 input and it is safe to run speculatively: measured 2026-08-28 on 238
  rectilinear frames, with no `--shape` it answered `no border found …; name one
  with --shape` and exited **0 having written nothing**.

* **`track` → `sam track`.** Needs a SAM checkpoint, which is never bundled and
  whose licence is a row in the audit table (§10) — SAM 2.1 is Apache-2.0 and
  **SAM 3 is Meta's own non-standard licence**, so they are accepted separately.
  `sam track --model` takes a *file*, not an id: there is no fetch on this route,
  and `model_licence` is what stops a run whose terms were never read.

Three things measured on this workstation rather than inferred, all 2026-08-28:

* **`sam mask` writes `<stem>.png`** — `frame_0001.png` beside `frame_0001.jpg`
  — which is the basename convention `masks/` already holds, and both readers
  take it. `sfm extract --masks` over 20 frames dropped **16 929** keypoints
  "over masked images: 20", 88 230 → 71 301 features, and the COLMAP convention
  `frame_0001.jpg.png` gave the byte-identical numbers. The two namings are
  interchangeable and §13.3 is closed.

* **`sam mask` has no per-frame channel and does not need one.** Two lines for
  the whole run — the image count, then `masks written: N, in <dir>` — and 238
  frames took **2.6 s**. The bar is the two ends of a short pass, not a counter
  we do not have.

* **Without `--replace` the masks are *intersected*** with what is already in
  the output folder. That is how a shape pass stacks on top of a model's masks,
  and it is why `replace` is a setting rather than something this module decides.

Pure module: no FastAPI import (§2.4).
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backend.core import frames as frame_files
from backend.core.defaults import SamDefaults, load_defaults
from backend.core.proc import ProcessAborted, iter_lines, release, spawn
from backend.core.steps import spirula

# ── The channel (§15) ────────────────────────────────────────────────────────
#
# `sam mask` prints two lines and no counter (measured; see the module docstring)
# so its bar is its two ends. `sam track` runs a network over every frame and
# prints one, in the `N/M` shape the rest of this tool family uses.
_IMAGES = re.compile(r"--\s*images:\s*(\d+)", re.I)
_WRITTEN = re.compile(r"masks written:\s*(\d+)", re.I)
_NO_BORDER = re.compile(r"no border found", re.I)
_FRAME_COUNTER = re.compile(r"\b(\d+)\s*/\s*(\d+)\b")

_ERROR_LINE = re.compile(r"\berror:", re.I)
_WARNING_LINE = re.compile(r"\bwarn(ing)?\b[: ]", re.I)

_P_START, _P_END = 0.02, 0.99

# The licences that must be accepted before a `track` run, and what each one
# actually is (§10). Two rows in the audit table, two separate acceptances: an
# Apache-2.0 checkpoint and a bespoke corporate licence are not the same
# question, and a single "I agree" covering both would be answering the harder
# one by accident.
MODEL_LICENCES: dict[str, str] = {
    "sam2.1": "SAM 2.1 — Apache-2.0.",
    "sam3": "SAM 3 — Meta's own licence, which is NOT Apache-2.0. Read it in "
            "full before using this checkpoint.",
}


def resolve_sam_settings(settings: dict) -> SamDefaults:
    """Overlay the per-project settings onto the app defaults (CLAUDE.md §4).

    Accepts the block nested under `sam` or flat, like every other resolver
    here: a run started from the mask panel sends it nested, one started from
    elsewhere may not.
    """
    base = load_defaults().sam.model_dump()
    incoming = settings or {}
    nested = incoming.get("sam")
    source = nested if isinstance(nested, dict) else incoming
    patch = {k: v for k, v in source.items() if k in base and v is not None}
    return SamDefaults.model_validate({**base, **patch})


def check_settings(sam: SamDefaults) -> Optional[str]:
    """The refusal message for a run that cannot work, or None.

    Decided from the settings alone and therefore checked before the exe, the
    frames and the first written byte — the same shape as `step_mesh`'s format
    precondition.
    """
    if sam.mode == "off":
        return ('Masking is off. Pick "Lens border / fixed shape" or '
                '"Track objects" before running it.')

    if sam.mode == "track":
        if not (sam.model or "").strip():
            return ("`sam track` needs a SAM checkpoint and there is none set. "
                    "The checkpoints are never bundled (§10) — download one by "
                    "hand and give its path in the mask panel.")
        if not Path(sam.model).is_file():
            return f"SAM checkpoint not found at: {sam.model}"
        if sam.model_licence not in MODEL_LICENCES:
            return ("The checkpoint's licence has not been accepted. SAM 2.1 is "
                    "Apache-2.0 and SAM 3 is Meta's own, non-standard licence — "
                    "they are accepted separately (§10). Say which one this "
                    "checkpoint is in the mask panel.")
        if not ((sam.text or "").strip() or (sam.neg_text or "").strip()):
            return ("`sam track` needs something to track. Name the objects in "
                    'the prompt — "person; car" — or switch to the lens-border '
                    "mode, which needs no prompt and no model.")
    return None


def build_command(
    frames_dir: Path, masks_dir: Path, sam: SamDefaults
) -> list[str]:
    """The full `spirula sam` command line for the selected mode.

    `--lang en` comes from `spirula.base_command`, not from here (§7.0.1).

    `sam`'s flags follow the `flag()` convention of every other tool except
    `--replace` and `--keep-prompted`, which are bare switches: they take no
    value at all, and handing one a `0` would be read as the next positional
    argument. Hence `spirula.switch` for those two.
    """
    if sam.mode == "shape":
        cmd = spirula.base_command("sam") + ["mask", str(frames_dir)]
        cmd += spirula.flag("out", str(masks_dir))
        if (sam.shape_spec or "").strip():
            cmd += spirula.flag("shape", sam.shape_spec.strip())
        cmd += spirula.flag("shrink", sam.shrink)
        cmd += spirula.flag("samples", sam.samples)
        cmd += spirula.flag("dark", sam.dark)
        # Without it the masks are *intersected* with what is already there,
        # which is how this stacks on top of a `track` run rather than undoing
        # it. The default is therefore the stacking one.
        cmd += spirula.switch("replace", sam.replace)
        return cmd

    cmd = spirula.base_command("sam") + ["track"]
    cmd += spirula.flag("model", str(Path(sam.model)))
    cmd += spirula.flag("frames", str(frames_dir))
    cmd += spirula.flag("out", str(masks_dir))
    if (sam.text or "").strip():
        cmd += spirula.flag("text", sam.text.strip())
    if (sam.neg_text or "").strip():
        cmd += spirula.flag("neg-text", sam.neg_text.strip())
    cmd += spirula.flag("detect-every", sam.detect_every)
    cmd += spirula.flag("threshold", sam.threshold)
    cmd += spirula.flag("nms", sam.nms)
    cmd += spirula.flag("max-size", sam.max_size)
    # The tool's own polarity is already what a reconstruction wants — the
    # prompted objects BLACK and everything else white — so this switch names
    # the *other* case, a prompt that describes the subject rather than the
    # distractors. There is no invert question to measure (§7.4).
    cmd += spirula.switch("keep-prompted", sam.keep_prompted)
    return cmd


def _classify(line: str) -> str:
    if _ERROR_LINE.search(line):
        return "ERROR"
    if _WARNING_LINE.search(line) or _NO_BORDER.search(line):
        return "WARNING"
    return "INFO"


def _write_result(project_path: Path, result: dict) -> None:
    """`analysis/mask_result.json` — beside the curation JSON, not in `masks/`.

    §5 says `masks/` holds "one greyscale PNG per frame, same basename", and it
    is a directory both `sfm auto` and `train` scan. A report file living in it
    would contradict the layout the two readers are pointed at, so it goes where
    the other per-run JSON already is. Both are cleared by a step 2 reset, so
    nothing is orphaned either way.
    """
    analysis = project_path / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    (analysis / "mask_result.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )


async def run_masking(project_path: Path, broadcast_fn, settings: dict) -> dict:
    """Write `masks/` with `spirula sam`, in the mode the settings selected."""
    sam = resolve_sam_settings(settings)

    refusal = check_settings(sam)
    if refusal:
        # Before the exe and before anything is written: decidable from the
        # settings alone, so it costs nothing to decide here.
        raise ValueError(refusal)

    version = spirula.read_version()
    await broadcast_fn("masks", "INFO", f"[masks] spirula {version}", progress=0.0)

    frames_dir = project_path / "frames"
    images = frame_files.list_frames(frames_dir)
    if not images:
        raise FileNotFoundError(
            f"No frames under {frames_dir}. Run step 2 first — the masks are "
            "one greyscale PNG per frame and there are no frames to mask."
        )

    # **Never a reset.** `masks/` is step 2's directory in §14.1's table and this
    # pass adds to it: without `--replace` the tool *intersects* with what is
    # already in the output folder, which is how a lens border stacks on top of
    # a `track` run. Clearing it here would silently turn every second run into
    # a first one.
    masks_dir = frame_files.masks_dir(project_path)
    masks_dir.mkdir(parents=True, exist_ok=True)
    existing = len(frame_files.list_mask_images(masks_dir))

    if sam.mode == "track":
        await broadcast_fn(
            "masks", "WARNING",
            f"[masks] {MODEL_LICENCES[sam.model_licence]} Accepted for "
            f"{Path(sam.model).name}. The checkpoint is never bundled with this "
            "app and was downloaded by hand (§10).",
        )

    if existing:
        await broadcast_fn(
            "masks", "INFO",
            f"[masks] masks/ already holds {existing} file(s). "
            + ("--replace: they are overwritten."
               if sam.replace else
               "They are *intersected* with this run's, not replaced — that is "
               "how a lens border stacks on top of a tracked object."),
        )

    cmd = build_command(frames_dir, masks_dir, sam)
    await broadcast_fn("masks", "INFO", f"[masks] Running: {' '.join(cmd)}")
    await broadcast_fn(
        "masks", "INFO",
        f"[masks] {len(images)} frames · "
        + ("lens border / fixed shape — no model, no download"
           if sam.mode == "shape" else
           f'tracking "{sam.text.strip()}" with {Path(sam.model).name}'),
        progress=_P_START,
    )

    loop = asyncio.get_running_loop()
    proc = spawn(cmd, project_path, cwd=str(project_path))

    tail: list[str] = []
    parsed: dict[str, Any] = {}
    progress = _P_START

    try:
        async for line in iter_lines(proc, loop):
            found = _IMAGES.search(line)
            if found:
                parsed["images_seen"] = int(found.group(1))
            found = _WRITTEN.search(line)
            if found:
                parsed["masks_written"] = int(found.group(1))
                progress = _P_END
            if _NO_BORDER.search(line):
                parsed["no_border"] = True

            # `sam mask` has no counter at all; `sam track` walks the frames and
            # prints one. Ride whichever is there and leave the bar where it is
            # otherwise — ProgressBar's indeterminate fallback is the honest
            # report for a pass that says nothing for 2.6 s (§15.3).
            counter = _FRAME_COUNTER.search(line)
            if counter and sam.mode == "track":
                done, total = int(counter.group(1)), int(counter.group(2))
                if 0 < done <= total:
                    progress = max(
                        progress, _P_START + (_P_END - _P_START) * done / total
                    )

            tail.append(line)
            del tail[:-40]
            await broadcast_fn("masks", _classify(line), line, progress=progress)

        returncode = await loop.run_in_executor(None, proc.wait)
    finally:
        killed = release(project_path, proc)

    if killed:
        raise ProcessAborted("The mask run was stopped by the user.")

    on_disk = frame_files.list_mask_images(masks_dir)
    frame_stems = {p.stem for p in images}
    matched = sum(1 for m in on_disk if m.stem in frame_stems)

    result: dict[str, Any] = {
        "exit_code": returncode,
        "spirula_version": version,
        "mode": sam.mode,
        "frames": len(images),
        "masks_before": existing,
        "masks": len(on_disk),
        "matched": matched,
        "replace": sam.replace,
        "shape_spec": sam.shape_spec if sam.mode == "shape" else None,
        "text": sam.text if sam.mode == "track" else None,
        "model": Path(sam.model).name if sam.mode == "track" else None,
        "model_licence": sam.model_licence if sam.mode == "track" else None,
        "command": cmd,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        **parsed,
    }
    # Written before the exit code is judged, like every other step's result: a
    # failed run's numbers are the ones somebody wants to read afterwards.
    _write_result(project_path, result)

    if returncode != 0:
        raise RuntimeError(
            f"spirula sam {sam.mode} exited {returncode}.\n"
            "Last output:\n" + "\n".join(tail[-15:])
        )

    if parsed.get("no_border"):
        # Exit 0 and nothing written — measured, and it is the ordinary answer
        # for a rectilinear capture rather than a failure. Naming `--shape` is
        # what the tool itself suggests.
        await broadcast_fn(
            "masks", "WARNING",
            "[masks] No lens border found, so nothing was written — which is "
            "the expected answer for a rectilinear capture. Name a shape "
            'yourself ("ellipse 0.5,0.5,0.49,0.49") to mask a region anyway.',
            progress=_P_END, data={"masks": result},
        )
        return result

    if not on_disk:
        raise RuntimeError(
            f"spirula sam {sam.mode} exited 0 but wrote nothing under "
            f"{masks_dir}. Last output:\n" + "\n".join(tail[-15:])
        )

    if matched < len(on_disk):
        # Both readers pair a mask to its frame by basename, and a mask that
        # pairs with nothing is read by nobody. Measured 2026-08-28: the COLMAP
        # convention `frame_0001.jpg.png` works too, so this only fires on a
        # genuinely foreign name.
        await broadcast_fn(
            "masks", "WARNING",
            f"[masks] {len(on_disk) - matched} mask(s) pair with no frame by "
            "basename and will be ignored by both `sfm auto` and `train`.",
        )

    await broadcast_fn(
        "masks", "SUCCESS",
        f"[masks] {len(on_disk)} masks in masks/, {matched} paired with a frame. "
        "Step 3 adopts them with no flag; step 4 is pointed at them explicitly.",
        progress=_P_END, data={"masks": result},
    )
    return result
