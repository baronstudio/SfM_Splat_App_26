"""Step 2 for an imported image set: conform, don't extract.

When `input/` holds a folder of stills instead of a video (§6.7), step 2 has no
frames to pull out of anything — the frames are already there. What is left is
everything else the extraction does on the way past: apply the output scale,
write the format the rest of the pipeline expects, honour the frame cap, and
leave `frames/` looking exactly as FFmpeg would have left it, so curation, the
gallery, RealityScan and the reset all carry on unchanged.

Three things this file is careful about.

**One subprocess, not nine hundred.** The import renamed the set to a
zero-padded sequence for this reason: FFmpeg's `image2` demuxer reads
`set_%04d.png` as a single input, so 900 images convert in one process with a
real `-progress` channel. Spawning ffmpeg per image would cost ~60 ms of
process creation each on Windows — a minute of pure overhead — and would report
nothing while doing it.

**A copy beats a re-encode.** When the scale is 100 % and the format is already
the one being written, the frames are hard-linked (falling back to a copy):
re-encoding a JPEG at `-qscale:v 2` is generation loss for no gain, and 900
20-megapixel PNGs is 18 GB that does not need to exist twice.

**The alpha channel is kept twice, and never beside the frames.** RealityScan
has no notion of alpha on a *source* image — its mask layers are a different
mechanism and a different workflow — so nothing goes into `frames/` for it to
find, or `-addFolder` would ingest it as a layer.

The channel exists to reach LichtFeld Studio, and it travels two ways at once
because they fail differently. It stays **inside the frames** (which is why
they are written as PNG rather than JPEG when alpha is kept — JPEG has no
channel to carry it), so it can ride through RS's COLMAP export in RS's own
undistorted geometry; `rc_alpha.py` measures whether it did. And it is
**extracted into `projects/<slug>/masks/`**, one greyscale PNG per frame, same
basename — a set LichtFeld Studio can read directly, and the thing to hand to
anything else that wants the mattes on their own.

Pure module: no FastAPI, `broadcast_fn` injected.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Optional

from backend.core import frames as frame_files
from backend.core import imageset
from backend.core.defaults import ExtractDefaults
from backend.core.proc import ProcessAborted, iter_lines, kill_tree, release, spawn

# `frame=  123` in an FFmpeg `-progress` block. The whole block is parsed by
# step_extract for the video path; here the frame counter is the only field
# worth reading, because the denominator is a file count, not a duration.
_FRAME_FIELD = re.compile(r"^frame=(\d+)$")

# Any other `-progress` field: a bare lowercase key and a value with no space
# in it. Same shape as step_extract's, and for the same reason — FFmpeg's own
# `frame=   20 fps=1.2 …` summary line must not be mistaken for one.
_PROGRESS_FIELD = re.compile(r"^[a-z0-9_]+=\S*$")

_LOG_EVERY_S = 2.0

# Only PNG carries an alpha channel this app will keep. TIFF can too, but
# nothing downstream reads a TIFF: the frames RS ingests and re-exports are PNG
# or JPEG. JPEG has no alpha at all.
ALPHA_SOURCE_SUFFIXES = {".png"}


def sequence_pattern(images: list[Path]) -> Optional[tuple[str, int]]:
    """`("set_%04d.png", 1)` when the set is one contiguous numbered sequence.

    Returns None when it is not — mixed formats, a gap in the numbering, a
    width that changes mid-way — and the caller falls back to converting file
    by file. An import always produces a clean sequence; a folder dropped into
    `input/` by hand may not, and reading a broken pattern as a good one would
    silently convert the first few images and stop.
    """
    if not images:
        return None

    suffixes = {p.suffix.lower() for p in images}
    if len(suffixes) != 1:
        return None

    prefix: Optional[str] = None
    width: Optional[int] = None
    numbers: list[int] = []
    for image in images:
        match = re.match(r"^(.*?)(\d+)$", image.stem)
        if not match:
            return None
        if prefix is None:
            prefix, width = match.group(1), len(match.group(2))
        elif match.group(1) != prefix or len(match.group(2)) != width:
            return None
        numbers.append(int(match.group(2)))

    start = numbers[0]
    if numbers != list(range(start, start + len(numbers))):
        return None

    return f"{prefix}%0{width}d{images[0].suffix.lower()}", start


def plan_output(
    images: list[Path], extract: ExtractDefaults, keep_alpha: bool
) -> dict:
    """What the conform is going to write, decided before anything is written.

    `keep_alpha` is the user's answer; `alpha` in the result is whether it
    applies — a set with no alpha channel cannot keep one, and the panel is
    told so rather than being quietly overruled.
    """
    suffixes = {p.suffix.lower() for p in images}
    alpha_capable = bool(suffixes) and suffixes <= ALPHA_SOURCE_SUFFIXES
    alpha = bool(keep_alpha and alpha_capable)

    out_suffix = ".png" if alpha else ".jpg"
    passthrough = (
        extract.scale_percent == 100
        and suffixes == {out_suffix}
    )
    return {
        "out_suffix": out_suffix,
        "alpha": alpha,
        "alpha_capable": alpha_capable,
        "passthrough": passthrough,
    }


def _link_or_copy(source: Path, target: Path) -> None:
    """Hard-link the frame, or copy it if the filesystem will not.

    A link because `frames/` and `input/` are two views of bytes that are
    identical in the passthrough case, and a project of 900 PNGs is 18 GB. It
    is safe against every operation the app performs: a reset deletes
    `frames/`, which drops one link and leaves `input/` — the directory §14
    says a reset never touches — holding the file. Nothing in the app ever
    writes *into* a frame.
    """
    import os
    import shutil

    target.unlink(missing_ok=True)
    try:
        os.link(source, target)
    except (OSError, NotImplementedError, AttributeError):
        shutil.copy2(source, target)


async def _run_ffmpeg(
    cmd: list[str],
    project_path: Path,
    broadcast_fn,
    total: int,
    label: str,
    progress_from: float,
    progress_to: float,
) -> None:
    """One FFmpeg run over an image sequence, reported against a file count."""
    loop = asyncio.get_running_loop()
    await broadcast_fn("extract", "INFO", f"[FFmpeg] Running: {' '.join(cmd)}")

    proc = spawn(cmd, project_path)
    output_lines: list[str] = []
    last_log = 0.0
    span = max(progress_to - progress_from, 0.0)

    try:
        async for line in iter_lines(proc, loop):
            match = _FRAME_FIELD.match(line)
            if not match:
                # Everything else on the pipe is either another `-progress`
                # field — `bitrate=`, `speed=`, none of which has a denominator
                # here — or real FFmpeg output, which belongs in the log.
                if line and not _PROGRESS_FIELD.match(line):
                    output_lines.append(line)
                    await broadcast_fn("extract", "INFO", line)
                continue

            done = int(match.group(1))
            ratio = min(done / total, 1.0) if total else None
            await broadcast_fn(
                "extract", "INFO", "",
                progress=(progress_from + span * ratio) if ratio is not None else None,
            )
            now = loop.time()
            if now - last_log >= _LOG_EVERY_S:
                last_log = now
                await broadcast_fn(
                    "extract", "INFO", f"[{label}] {done}/{total} images"
                )

        returncode = await loop.run_in_executor(None, proc.wait)
    except asyncio.CancelledError:
        kill_tree(proc)
        raise
    finally:
        killed = release(project_path, proc)

    if killed:
        raise ProcessAborted("FFmpeg was stopped by the user.")
    if returncode != 0:
        tail = "\n".join(output_lines[-20:]) or "(no output)"
        raise RuntimeError(
            f"FFmpeg exited with code {returncode} while conforming the image set.\n"
            f"Last output:\n{tail}"
        )


def _scale_args(extract: ExtractDefaults) -> list[str]:
    from backend.core.steps.step_extract import build_scale_filter

    clause = build_scale_filter(extract.scale_percent)
    return ["-vf", clause] if clause else []


def _encode_args(plan: dict, extract: ExtractDefaults) -> list[str]:
    if plan["out_suffix"] == ".png":
        # The PNG encoder keeps the RGBA it is handed; the compression level is
        # a speed/size trade with no effect on the pixels, unlike -qscale:v.
        return ["-compression_level", "6"]
    return ["-qscale:v", str(extract.quality)]


async def run_conform(
    project_path: Path,
    broadcast_fn,
    settings: dict,
    set_dir: Path,
    extract: ExtractDefaults,
    ffmpeg_path: str,
) -> dict:
    """Conform an imported image set into `frames/`.

    Called by `run_extract` once it has resolved the input to a set rather than
    a video, and once `frames/` has been cleared — the reset is step 2's, not
    this function's, so both branches clear exactly the same artefacts.
    """
    images = imageset.set_images(set_dir)
    if not images:
        raise FileNotFoundError(f"No image found in {set_dir}")

    source_count = len(images)
    if extract.max_frames > 0 and source_count > extract.max_frames:
        images = images[: extract.max_frames]
        await broadcast_fn(
            "extract", "INFO",
            f"[conform] Capped at {extract.max_frames} of {source_count} images "
            "(max frames).",
        )

    plan = plan_output(images, extract, extract.keep_alpha)
    frames_dir = project_path / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    info = imageset.read_image_info(images[0])
    await broadcast_fn(
        "extract", "INFO",
        f"[conform] {len(images)} image(s) from '{set_dir.name}' — "
        f"{info.get('width')}x{info.get('height')}, "
        f"writing {plan['out_suffix']} at {extract.scale_percent}%",
    )

    if extract.keep_alpha and not plan["alpha_capable"]:
        await broadcast_fn(
            "extract", "WARNING",
            "[conform] Alpha was requested but this set has no PNG alpha channel "
            "to keep — the frames are written as JPEG.",
        )

    pattern = sequence_pattern(images)

    if plan["passthrough"]:
        await broadcast_fn(
            "extract", "INFO",
            "[conform] Scale is 100 % and the format already matches — the frames "
            "are linked rather than re-encoded, which costs no disk and no quality.",
        )
        loop = asyncio.get_running_loop()
        for index, image in enumerate(images, start=1):
            await loop.run_in_executor(
                None, _link_or_copy, image, frames_dir / image.name
            )
            if index % 25 == 0 or index == len(images):
                await broadcast_fn(
                    "extract", "INFO", "",
                    progress=0.05 + 0.85 * index / len(images),
                )
    elif pattern is not None:
        template, start = pattern
        cmd = [
            ffmpeg_path, "-y",
            "-progress", "pipe:1", "-nostats",
            "-f", "image2",
            "-start_number", str(start),
            "-i", str(set_dir / template),
            *(["-frames:v", str(len(images))] if len(images) < source_count else []),
            *_scale_args(extract),
            *_encode_args(plan, extract),
            "-start_number", str(start),
            str(frames_dir / (template.rsplit(".", 1)[0] + plan["out_suffix"])),
        ]
        await _run_ffmpeg(
            cmd, project_path, broadcast_fn, len(images), "conform", 0.05, 0.90
        )
    else:
        await broadcast_fn(
            "extract", "WARNING",
            "[conform] The set is not one contiguous numbered sequence (mixed "
            "formats, or a gap in the numbering), so it is converted image by "
            "image — slower, same result.",
        )
        loop = asyncio.get_running_loop()
        for index, image in enumerate(images, start=1):
            target = frames_dir / (image.stem + plan["out_suffix"])
            cmd = [
                ffmpeg_path, "-y", "-v", "error",
                "-i", str(image),
                *_scale_args(extract),
                *_encode_args(plan, extract),
                str(target),
            ]
            proc = spawn(cmd, project_path)
            try:
                returncode = await loop.run_in_executor(None, proc.wait)
            except asyncio.CancelledError:
                kill_tree(proc)
                raise
            finally:
                killed = release(project_path, proc)
            if killed:
                raise ProcessAborted("FFmpeg was stopped by the user.")
            if returncode != 0:
                raise RuntimeError(
                    f"FFmpeg exited with code {returncode} on {image.name}."
                )
            if index % 10 == 0 or index == len(images):
                await broadcast_fn(
                    "extract", "INFO", "",
                    progress=0.05 + 0.85 * index / len(images),
                )

    written = frame_files.list_frames(frames_dir)

    mask_count = 0
    if plan["alpha"]:
        mask_count = await _extract_alpha(
            project_path, frames_dir, written, ffmpeg_path, broadcast_fn
        )

    _write_meta(
        project_path,
        set_dir=set_dir,
        extract=extract,
        plan=plan,
        frame_count=len(written),
        source_count=source_count,
        mask_count=mask_count,
        info=info,
    )

    await broadcast_fn(
        "extract", "SUCCESS",
        f"Conformed {len(written)} frames from '{set_dir.name}' → {frames_dir}"
        + (f" (+{mask_count} alpha images → masks/)" if mask_count else ""),
        progress=1.0,
    )
    return {
        "frame_count": len(written),
        "frames_dir": str(frames_dir),
        "source": set_dir.name,
        "source_kind": "images",
        "alpha": plan["alpha"],
        "mask_count": mask_count,
    }


async def _extract_alpha(
    project_path: Path,
    frames_dir: Path,
    written: list[Path],
    ffmpeg_path: str,
    broadcast_fn,
) -> int:
    """Write the alpha channel of every frame as a greyscale PNG in `masks/`.

    `alphaextract` is the whole conversion — the channel already means what a
    mask means, opaque where the subject is. One FFmpeg pass over the sequence,
    for the same reason the conform itself is one pass.

    The masks take the frame's own basename, in a directory of their own: that
    is what LichtFeld Studio reads (`masks/` mirroring the image names), and it
    keeps them out of the folder RealityScan ingests.
    """
    if not written:
        return 0

    masks = frame_files.masks_dir(project_path)
    masks.mkdir(parents=True, exist_ok=True)
    pattern = sequence_pattern(written)

    await broadcast_fn(
        "extract", "INFO",
        f"[alpha] Extracting {len(written)} alpha image(s) into masks/.",
    )

    if pattern is not None:
        template, start = pattern
        cmd = [
            ffmpeg_path, "-y",
            "-progress", "pipe:1", "-nostats",
            "-f", "image2",
            "-start_number", str(start),
            "-i", str(frames_dir / template),
            # `format=gray` is explicit rather than implied: the image2 muxer
            # would otherwise write greyscale or paletted depending on the
            # build, and a palette is one more thing that can go wrong for a
            # file whose only job is to be read as luminance.
            "-vf", "alphaextract,format=gray",
            "-start_number", str(start),
            str(masks / template),
        ]
        await _run_ffmpeg(
            cmd, project_path, broadcast_fn, len(written), "alpha", 0.90, 0.99
        )
    else:
        loop = asyncio.get_running_loop()
        for index, frame in enumerate(written, start=1):
            cmd = [
                ffmpeg_path, "-y", "-v", "error",
                "-i", str(frame),
                "-vf", "alphaextract,format=gray",
                str(masks / f"{frame.stem}.png"),
            ]
            proc = spawn(cmd, project_path)
            try:
                await loop.run_in_executor(None, proc.wait)
            except asyncio.CancelledError:
                kill_tree(proc)
                raise
            finally:
                killed = release(project_path, proc)
            if killed:
                raise ProcessAborted("FFmpeg was stopped by the user.")
            if index % 10 == 0 or index == len(written):
                await broadcast_fn(
                    "extract", "INFO", "",
                    progress=0.90 + 0.09 * index / len(written),
                )

    return len(frame_files.list_mask_images(masks))


def _write_meta(
    project_path: Path,
    set_dir: Path,
    extract: ExtractDefaults,
    plan: dict,
    frame_count: int,
    source_count: int,
    mask_count: int,
    info: dict,
) -> None:
    """`analysis/extract.json` and `analysis/probe.json` for an image set.

    Both files keep the shape the rest of the app reads, with the fields that
    only a video can answer left null rather than invented:

    * `working_fps` is null and `input_video` is null, which is what sends
      curation to the frames-only cut detector — the right detector here, since
      there is no video to run PySceneDetect on and no timecode to map onto.
    * `probe.json` is marked `synthetic` and carries the nominal 30 img/s the
      panel counts a duration with (§6.7). It is not an ffprobe reading and
      does not pretend to be one.
    """
    analysis_dir = project_path / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)

    (analysis_dir / "extract.json").write_text(
        json.dumps({
            "source_kind": "images",
            "image_set": set_dir.name,
            "image_set_path": str(set_dir),
            "source_image_count": source_count,
            "working_fps": None,
            "fps_explanation": (
                "Imported image set — every image is a frame, so no fps policy applies."
            ),
            "input_video": None,
            "mpdecimate": False,
            "quality": extract.quality,
            "scale_percent": extract.scale_percent,
            "max_frames": extract.max_frames,
            "capture_preset": extract.capture_preset,
            "frame_count": frame_count,
            "frame_format": plan["out_suffix"],
            "passthrough": plan["passthrough"],
            "alpha": plan["alpha"],
            "mask_count": mask_count,
            "hwaccel": "none",
            "hwaccel_fell_back": False,
            "scene_scores": False,
            "scene_score_frames": 0,
        }, indent=2),
        encoding="utf-8",
    )

    (analysis_dir / "probe.json").write_text(
        json.dumps({
            "synthetic": True,
            "source_kind": "images",
            "codec": plan["out_suffix"].lstrip("."),
            "width": info.get("width"),
            "height": info.get("height"),
            "fps": imageset.NOMINAL_FPS,
            "duration_s": round(frame_count / imageset.NOMINAL_FPS, 2),
            "frame_count": frame_count,
        }, indent=2),
        encoding="utf-8",
    )
