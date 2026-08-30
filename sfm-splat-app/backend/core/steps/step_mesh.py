"""step_mesh.py — step 5: `spirula mesh`.

One command turns the trained splat into a surface (CLAUDE.md §7.8):

    spirula --lang en mesh <checkpoint> --data <project>/sfm
            --output <project>/mesh/mesh --format glb --color texture

`<checkpoint>` may be a run directory, a `*.ckpt` directory or a `splat.ply`;
this step passes the **splat.ply** it located itself, for the same reason step 4
globs its checkpoint rather than assuming one (§7.6): the file is the thing that
exists, and `resolve_splat` has already proved it is non-empty - and it is what
prefers `train/crop/splat.ply` when a crop was applied (§7.6b). `--data` gives the
cameras that decide occupancy and colour, and it is the same `sfm/` step 4
trained against.

Four things here are not the obvious implementation:

* **`--output` is not optional.** Its default is `<checkpoint>/mesh`, and
  `<checkpoint>` resolves to the `.ckpt` **directory** — measured, a run given
  the run directory wrote `train/run/step-000007000.ckpt/mesh.glb`, inside the
  one directory `--save-only-latest-checkpoint` deletes on the next training
  run. A 204-second mesh destroyed by the next click on step 4 (§12,
  2026-08-27). The flag is emitted here and is not a setting.

* **Two format/colour pairs are a precondition, not a warning.** Measured:
  `--format glb,ply --color texture` answered `PLY does not support textured
  meshes` and exited **1 having written nothing at all — not even the glb it
  could have made**. So the refusal is checked before the run rather than
  reported after it.

* **The images are junctioned into the dataset for the length of the run.**
  `mesh` reads them through `<dataset>/images/<name>` and has no
  `--image-dir`, exactly like `geometry` (§7.5) — measured 2026-08-30, a run
  whose checkpoint was the crop's `train/crop/splat.ply` died on
  `ColmapParser: ...sfm/images/frame_0001.jpg does not exist` at **exit 1**
  having written nothing. A checkpoint under `train/run/` escapes that only
  because the tool reads the `image_dir` recorded in the run's own
  `config.json`, which is why step 5 worked before the crop existed and
  failed on every cropped project after it. `ImageJunction` is shared with
  the geometry pass and removed in `__exit__`, so §5's layout on disk is
  unchanged and there is still one copy of the frames.

* **86 % of the log is one counter line.** The three camera loops —
  `occupancy`, `texel density` and `color` — print `cameras rendered: N/total`
  every four cameras, and the reference capture below printed **360 of 419
  lines** that way. The LiveLog keeps 500, so they are dropped from the bus
  except at the end of each block, exactly as `_EXTRACT_NOISE` drops step 3's
  per-image narration (§12, 2026-08-27). They still drive the bar: the progress
  rides on a message-less broadcast, which `websocket.broadcast` omits from the
  payload and the store therefore never logs.

Pure module: no FastAPI import (§2.4).
"""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backend.core import colmap
from backend.core.dataset_images import ImageJunction
from backend.core.defaults import MeshDefaults, load_defaults
from backend.core.proc import ProcessAborted, iter_lines, release, spawn
from backend.core.project_ops import reset_steps
from backend.core.steps import spirula, step_train
from backend.core.steps.step_crop import resolve_splat

# ── The tagged stdout channel (§7.8) ─────────────────────────────────────────
#
# Every line is `[meshing] <phase>: <detail>`, and the phases are **not
# monotone**: `merge`, `cull unseen` and `cleanup` run twice, and `bisection`
# re-evaluates the occupancy grid once per iteration, so `occupancy:` counter
# lines appear *inside* the bisection phase. Captured on this workstation, whole
# run in `docs/spirula/mesh-run.txt`.
_TAGGED = re.compile(r"^\[meshing\]\s+([^:]+):\s*(.*)$")
_CAMERA_COUNTER = re.compile(r"^cameras rendered:\s*(\d+)\s*/\s*(\d+)")

# Where each phase starts. The order is the canonical one and a phase never
# moves the bar backwards, which is what keeps the repeated merge/cleanup rounds
# and the nested occupancy loops honest. The shares are roughly the reference
# run's distribution — 18.15 s over 98 025 gaussians and 238 cameras, of which
# merge was 6.2 s and texture 2.3 s — and they are a *position*, not a promise:
# on the 204 s reference mesh of §7.8 texture alone was 30.9 s. Where a phase
# has no number at all, ProgressBar's 10-second indeterminate fallback is the
# honest report (§15.3).
_PHASE_START: dict[str, float] = {
    "loading": 0.02,
    "point cloud": 0.06,
    "delaunay": 0.10,
    "occupancy": 0.16,       # camera loop
    "cut edges": 0.26,
    "bisection": 0.29,       # nests a camera loop per iteration
    "marching tets": 0.38,
    "merge": 0.41,
    "cull unseen": 0.55,
    "cleanup": 0.58,
    "quality": 0.66,
    "orient": 0.68,
    "texel density": 0.69,   # camera loop
    "uv": 0.78,
    "bake": 0.83,
    "color": 0.85,           # camera loop
    "texture": 0.94,
    "stats": 0.97,
    "wrote": 0.98,
    "done": 0.99,
}
_PHASE_ORDER: tuple[str, ...] = tuple(_PHASE_START)
_PHASE_INDEX = {name: i for i, name in enumerate(_PHASE_ORDER)}
_P_START, _P_END = 0.01, 0.99

# ── The result block ─────────────────────────────────────────────────────────
# `stats:` and `done:` both end on the same pair, so it gets its own pattern —
# only `stats:` carries the component count and the edge tallies beside it.
_VERTS_FACES = re.compile(r"vertices:\s*(\d+),\s*faces:\s*(\d+)", re.I)
_COMPONENTS = re.compile(r"components:\s*(\d+)", re.I)
_BOUNDARY_EDGES = re.compile(r"boundary:\s*(\d+)", re.I)
_NON_MANIFOLD_EDGES = re.compile(r"non-manifold:\s*(\d+)", re.I)
_MIS_ORIENTED_EDGES = re.compile(r"mis-oriented:\s*(\d+)", re.I)
# `wrote: <path> -- vertices: N, faces: M`, one per requested format.
_WROTE = re.compile(r"^(.+?)\s+--\s+vertices:\s*(\d+)", re.I)
_DONE_TOTAL = re.compile(r"\(total\s*([\d.]+)\s*s\)", re.I)
# `UV: texture size chosen for you: 4096 (budget 1.05515e+07 texels)` when
# --texture-size is 0, and the bake's own `the 4096x4096 texture is finished`.
_TEXTURE_CHOSEN = re.compile(r"texture size chosen for you:\s*(\d+)", re.I)
_TEXTURE_FINISHED = re.compile(r"the\s*(\d+)\s*x\s*(\d+)\s*texture is finished", re.I)
# `bake: covered texels: 4490440/16777216 (26.8%)` — the texel coverage §8 asks
# the dashboard for.
_COVERED_TEXELS = re.compile(
    r"covered texels:\s*(\d+)\s*/\s*(\d+)\s*\(([\d.]+)\s*%\)", re.I
)
_GAUSSIANS = re.compile(r"^Gaussians:\s*(\d+)", re.I)
_CAMERAS_USED = re.compile(r"cameras used:\s*(\d+)\s*/\s*(\d+)", re.I)

_ERROR_LINE = re.compile(r"\berror:", re.I)
_WARNING_LINE = re.compile(r"\bwarn(ing)?\b[: ]", re.I)

# The build's own defaults, off `docs/spirula/mesh-help.txt`. `mesh` has no
# presets — unlike `sfm auto` and `train` — so naming a flag at its default
# undoes nothing, but the command line is read by a human in the log and a knob
# nobody moved is noise on it. `--format`, `--color` and `--output` are absent
# on purpose: they are always sent.
_BUILD_DEFAULTS: dict[str, Any] = {
    "texture_size": 0,
    "max_cameras": -1,
    "max_grid_res": 512,
    "cull_unseen": True,
    "floater_min_faces": 100,
    "quality_iters": 3,
    "num_threads": 0,
}

# The two pairs the tool refuses, in its own words. Checked before the run
# because the refusal costs every format, not just the offending one.
_REFUSALS: tuple[tuple[str, str, str], ...] = (
    ("ply", "texture",
     "PLY carries no texture. Drop PLY from the formats, or switch the colour "
     "to vertex or none."),
    ("obj", "vertex",
     "OBJ carries no vertex colours. Drop OBJ from the formats, or switch the "
     "colour to texture or none."),
)

# `--output <base>`; the tool appends the extension of each format.
OUTPUT_BASENAME = "mesh"


def resolve_mesh_settings(settings: dict) -> MeshDefaults:
    """Overlay the per-project settings onto the app defaults (CLAUDE.md §4).

    Accepts the block nested under `mesh` or flat, like every other resolver
    here: a run started from the step panel sends it nested, one started from
    elsewhere may not.
    """
    base = load_defaults().mesh.model_dump()
    incoming = settings or {}
    nested = incoming.get("mesh")
    source = nested if isinstance(nested, dict) else incoming
    patch = {k: v for k, v in source.items() if k in base and v is not None}
    return MeshDefaults.model_validate({**base, **patch})


def check_formats(mesh: MeshDefaults) -> Optional[str]:
    """The refusal message for an impossible format/colour pair, or None.

    Measured 2026-08-27: `--format glb,ply --color texture` exits **1 having
    written nothing at all**, not even the glb it was also asked for. So this is
    a precondition and not a per-format skip to warn about — the UI enforces the
    same rule, and this is the backend half that a run started from anywhere
    else still hits.
    """
    if not mesh.formats:
        return "No output format selected — pick at least one of PLY, OBJ, glTF, GLB."
    for fmt, color, reason in _REFUSALS:
        if fmt in mesh.formats and mesh.color == color:
            return f"--format {fmt} with --color {color} is refused: {reason}"
    return None


def format_arguments(mesh: MeshDefaults) -> list[str]:
    """The `--format` list, with the texture encoding attached where it applies.

    `mesh --help`: "With --color texture a format may carry a texture encoding:
    glb+png (default), glb+jpg (JPEG q95), glb+jpeg75 (JPEG q75)". Only `glb` is
    documented as carrying one, so only `glb` gets one — and only when it would
    change something, because `glb+png` is what the build already does.
    """
    out: list[str] = []
    for fmt in mesh.formats:
        if (fmt == "glb" and mesh.color == "texture"
                and mesh.texture_encoding != "png"):
            out.append(f"{fmt}+{mesh.texture_encoding}")
        else:
            out.append(fmt)
    return out


def _moved_from_build_default(mesh: MeshDefaults) -> list[tuple[str, Any]]:
    """The knobs the user actually moved — the only ones worth naming."""
    return [
        (name, getattr(mesh, name))
        for name, build_value in _BUILD_DEFAULTS.items()
        if getattr(mesh, name) != build_value
    ]


def build_command(
    checkpoint: Path,
    dataset_dir: Optional[Path],
    mesh_dir: Path,
    mesh: MeshDefaults,
) -> list[str]:
    """The full `spirula mesh` command line.

    `--lang en` comes from `spirula.base_command`, not from here (§7.0.1).
    """
    cmd = spirula.base_command("mesh") + [str(checkpoint)]

    if dataset_dir is not None:
        cmd += spirula.flag("data", str(dataset_dir))
    else:
        # A bare switch, like `sfm auto`'s `--no-masks`: it takes no value, and
        # handing it a `0` would be read as the next positional argument.
        cmd += spirula.switch("no-data", True)

    # Never left to default: `<checkpoint>/mesh` writes inside the `.ckpt`
    # directory the next training run deletes (§12, 2026-08-27).
    cmd += spirula.flag("output", str(mesh_dir / OUTPUT_BASENAME))
    cmd += spirula.flag("format", ",".join(format_arguments(mesh)))
    cmd += spirula.flag("color", mesh.color)

    # 0 means "let the build decide", and here that is not the same number in
    # both cases: `--iso` defaults to 0.5 with cameras and 0.2 without, so a
    # literal in this file would silently pick one of them.
    if mesh.iso > 0:
        cmd += spirula.flag("iso", mesh.iso)

    cmd += spirula.flags(_moved_from_build_default(mesh))
    return cmd


def _classify(line: str) -> str:
    if _ERROR_LINE.search(line):
        return "ERROR"
    if _WARNING_LINE.search(line):
        return "WARNING"
    return "INFO"


def _phase_progress(tag: str, detail: str, state: dict) -> Optional[float]:
    """Where the bar goes for this line, or None if it says nothing about it.

    A phase only ever moves the bar *forwards*: the tool revisits `merge`,
    `cull unseen` and `cleanup`, and it prints `occupancy:` counter lines from
    inside `bisection`, so a naive lookup would send the bar backwards three
    times in a run.
    """
    index = _PHASE_INDEX.get(tag)
    if index is None:
        return None
    if index > state.get("phase_index", -1):
        state["phase_index"] = index
        state["phase"] = tag

    current = state.get("phase")
    if current is None:
        return None
    start = _PHASE_START[current]
    position = _PHASE_INDEX[current] + 1
    end = (_PHASE_START[_PHASE_ORDER[position]]
           if position < len(_PHASE_ORDER) else _P_END)

    counter = _CAMERA_COUNTER.match(detail)
    if counter:
        done, total = int(counter.group(1)), int(counter.group(2))
        if total > 0:
            start += (end - start) * min(done / total, 1.0)

    value = min(max(start, state.get("progress", _P_START)), _P_END)
    state["progress"] = value
    return value


async def _clear_previous_run(project_path: Path, broadcast_fn) -> None:
    """Reset step 5 — after the exe and the checkpoint are located, never before.

    §14.1: locate the tool and the input first, delete second. Step 5 owns
    `export/` as well as `mesh/`, so this clears both — that is the documented
    meaning of resetting step 5, not a surprise.
    """
    removed = reset_steps(project_path, [5])
    if removed:
        await broadcast_fn(
            "mesh", "INFO",
            f"[mesh] Cleared the previous run: {', '.join(removed)}",
            progress=0.0,
        )


def find_outputs(mesh_dir: Path) -> list[Path]:
    """The mesh files on disk, whatever formats the run was asked for.

    Read off the folder rather than derived from the settings: a run the user
    aborted half way through the format list leaves some of them, and the
    honest answer is what is there.
    """
    if not mesh_dir.is_dir():
        return []
    suffixes = {".ply", ".obj", ".gltf", ".glb"}
    return sorted(
        p for p in mesh_dir.iterdir()
        if p.is_file() and p.suffix.lower() in suffixes and p.stat().st_size > 0
    )


def _write_result(mesh_dir: Path, result: dict) -> None:
    mesh_dir.mkdir(parents=True, exist_ok=True)
    (mesh_dir / "mesh_result.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )


async def run_mesh(project_path: Path, broadcast_fn, settings: dict) -> dict:
    """Step 5: extract a surface mesh with `spirula mesh`."""
    mesh = resolve_mesh_settings(settings)

    refusal = check_formats(mesh)
    if refusal:
        # Before the exe, before the delete, before the 204 seconds: this one is
        # decidable from the settings alone.
        raise ValueError(refusal)

    # The exe next, and it fails with the path it looked for (§2.2). Before any
    # delete, and before anything is written.
    version = spirula.read_version()
    await broadcast_fn("mesh", "INFO", f"[mesh] spirula {version}", progress=0.0)

    train_dir = project_path / "train"
    checkpoint, cropped = resolve_splat(train_dir)
    if checkpoint is None:
        raise FileNotFoundError(
            f"No trained splat under {train_dir}. Run step 4 first — the mesh "
            "is extracted from the gaussians, not from the sparse model."
        )

    if cropped:
        # A mesh of 700 000 gaussians and a mesh of the 300 000 that survived a
        # crop are the same command line and very different results, so the run
        # names its own input (CLAUDE.md 7.6b). `mesh_result.json` keeps the
        # path too, and deleting `train/crop/` is what meshes the full splat.
        await broadcast_fn(
            "mesh", "INFO",
            f"[mesh] cropped splat: {checkpoint.relative_to(project_path)}, "
            f"{step_train.splat_count(checkpoint) or '?'} gaussians",
        )

    dataset_dir: Optional[Path] = project_path / "sfm"
    if not mesh.use_cameras:
        dataset_dir = None
        await broadcast_fn(
            "mesh", "INFO",
            "[mesh] --no-data: meshing from the gaussian densities alone. No "
            "camera occupancy, no camera colour, and --iso defaults to 0.2 "
            "instead of 0.5.",
        )
    elif colmap.find_model(dataset_dir) is None:
        # The cameras are what decide occupancy and colour, so a missing model
        # is a different mesh rather than a broken one — but silently becoming
        # the density-only mesh would be the wrong kind of helpful.
        raise FileNotFoundError(
            f"No sparse model under {dataset_dir}, and the cameras are what "
            "decide occupancy and colour. Run step 3 first, or turn off "
            "\"Use the cameras\" to mesh from the gaussian densities alone."
        )

    if mesh.color == "texture" and dataset_dir is None:
        # Not a refusal: the tool has not been measured on this pair, and the
        # rule here is to name a flag's consequence rather than invent one.
        await broadcast_fn(
            "mesh", "WARNING",
            "[mesh] --color texture with --no-data: the texture is baked from "
            "the camera renders, and there are no cameras. Untested pairing.",
        )

    await _clear_previous_run(project_path, broadcast_fn)

    mesh_dir = project_path / "mesh"
    mesh_dir.mkdir(parents=True, exist_ok=True)

    cmd = build_command(checkpoint, dataset_dir, mesh_dir, mesh)
    await broadcast_fn("mesh", "INFO", f"[mesh] Running: {' '.join(cmd)}")
    await broadcast_fn(
        "mesh", "INFO",
        f"[mesh] {checkpoint.parent.name} · "
        f"format {', '.join(format_arguments(mesh))} · colour {mesh.color}"
        + (f" · texture {mesh.texture_size}px" if mesh.texture_size else
           " · texture size from the observed texel budget"),
        progress=_P_START,
    )

    loop = asyncio.get_running_loop()
    # `mesh` resolves `<dataset>\images\<name>` exactly as `geometry` does,
    # and has no `--image-dir` either — `mesh --help` lists none, whatever the
    # parser's own "set --image-dir if needed" says. Measured 2026-08-30: the
    # crop's `train/crop/splat.ply` died on `ColmapParser: <project>\sfm\images\
    # frame_0001.jpg does not exist` at **exit 1**, having written nothing. A
    # checkpoint under `train/run/` escapes it only because the tool reads the
    # `image_dir` recorded in that run's own `config.json`, which is why step 5
    # worked before the crop existed and not after it. So the same junction
    # step 4's geometry pass uses, for the length of this command and no longer
    # (§7.5, §7.8).
    frames_dir = project_path / "frames"
    images = ExitStack()
    with images:
        if dataset_dir is not None and frames_dir.is_dir():
            junction = images.enter_context(
                ImageJunction(dataset_dir, frames_dir))
            await broadcast_fn(
                "mesh", "INFO",
                f"[mesh] {junction.link.name}/ linked to frames/ for the length "
                "of this run: `mesh` reads the images through the dataset folder "
                "and has no --image-dir. Removed again when it finishes — there "
                "is still only one copy of the frames on disk (§5.2).",
                progress=_P_START,
            )

        proc = spawn(cmd, project_path, cwd=str(project_path))

        tail: list[str] = []
        parsed: dict[str, Any] = {}
        written: list[str] = []
        state: dict[str, Any] = {"progress": _P_START}

        try:
            async for line in iter_lines(proc, loop):
                match = _TAGGED.match(line)
                tag = match.group(1).strip().lower() if match else None
                detail = match.group(2).strip() if match else line.strip()

                counter = _CAMERA_COUNTER.match(detail) if match else None
                progress = _phase_progress(tag, detail, state) if tag else None

                # 360 of the reference run's 419 lines were this counter, against a
                # 500-line LiveLog. Only the end of each block survives; the rest
                # ride the bar with no text, which `broadcast` omits from the
                # payload and the store therefore never logs.
                if counter and counter.group(1) != counter.group(2):
                    await broadcast_fn("mesh", "INFO", "", progress=progress)
                    continue

                tail.append(line)
                del tail[:-40]
                await broadcast_fn("mesh", _classify(line), line, progress=progress)

                if tag == "loading":
                    found = _GAUSSIANS.match(detail)
                    if found:
                        parsed["gaussians"] = int(found.group(1))
                    found = _CAMERAS_USED.search(detail)
                    if found:
                        parsed["cameras_used"] = int(found.group(1))
                        parsed["cameras_available"] = int(found.group(2))
                elif tag == "uv":
                    found = _TEXTURE_CHOSEN.search(detail)
                    if found:
                        parsed["texture_size"] = int(found.group(1))
                elif tag == "bake":
                    found = _TEXTURE_FINISHED.search(detail)
                    if found:
                        parsed["texture_size"] = int(found.group(1))
                    found = _COVERED_TEXELS.search(detail)
                    if found:
                        parsed["texels_covered"] = int(found.group(1))
                        parsed["texels_total"] = int(found.group(2))
                        parsed["texel_coverage_pct"] = float(found.group(3))
                elif tag == "stats":
                    found = _VERTS_FACES.search(detail)
                    if found:
                        parsed["vertices"] = int(found.group(1))
                        parsed["faces"] = int(found.group(2))
                    for key, pattern in (
                        ("components", _COMPONENTS),
                        ("boundary_edges", _BOUNDARY_EDGES),
                        ("non_manifold_edges", _NON_MANIFOLD_EDGES),
                        ("mis_oriented_edges", _MIS_ORIENTED_EDGES),
                    ):
                        found = pattern.search(detail)
                        if found:
                            parsed[key] = int(found.group(1))
                elif tag == "wrote":
                    found = _WROTE.match(detail)
                    if found:
                        written.append(found.group(1).strip())
                elif tag == "done":
                    found = _VERTS_FACES.search(detail)
                    if found:
                        parsed["vertices"] = int(found.group(1))
                        parsed["faces"] = int(found.group(2))
                    found = _DONE_TOTAL.search(detail)
                    if found:
                        parsed["elapsed_s"] = float(found.group(1))

            returncode = await loop.run_in_executor(None, proc.wait)
        finally:
            killed = release(project_path, proc)

    if killed:
        raise ProcessAborted("The meshing was stopped by the user.")

    outputs = find_outputs(mesh_dir)
    result: dict[str, Any] = {
        "exit_code": returncode,
        "spirula_version": version,
        "checkpoint": str(checkpoint.relative_to(project_path)),
        "formats": format_arguments(mesh),
        "color": mesh.color,
        "cameras_requested": mesh.use_cameras,
        "iso": mesh.iso or None,
        "files": [
            {
                "filename": p.name,
                "bytes": p.stat().st_size,
                "path": str(p.relative_to(project_path)),
            }
            for p in outputs
        ],
        # What the tool said it wrote, kept beside what is on disk: they differ
        # when a format failed, and the pair is what says which one.
        "written": written,
        "command": cmd,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        **parsed,
    }
    # Written before the exit code is judged: a failed run's numbers are exactly
    # the ones somebody will want to read afterwards.
    _write_result(mesh_dir, result)

    if returncode != 0:
        raise RuntimeError(
            f"spirula mesh exited {returncode}.\n"
            "Last output:\n" + "\n".join(tail[-15:])
        )

    if not outputs:
        raise RuntimeError(
            f"spirula mesh exited 0 but wrote nothing under {mesh_dir}. "
            "Last output:\n" + "\n".join(tail[-15:])
        )

    summary = " · ".join(
        part for part in (
            f"{parsed['vertices']:,} vertices" if "vertices" in parsed else None,
            f"{parsed['faces']:,} faces" if "faces" in parsed else None,
            f"{parsed['components']:,} components" if "components" in parsed else None,
            f"{parsed['texture_size']}px texture at "
            f"{parsed['texel_coverage_pct']:.1f} % coverage"
            if "texture_size" in parsed and "texel_coverage_pct" in parsed else None,
            f"{parsed['elapsed_s']:.1f} s" if "elapsed_s" in parsed else None,
            ", ".join(p.name for p in outputs),
        ) if part
    )
    await broadcast_fn(
        "mesh", "SUCCESS", f"[mesh] {summary}.",
        progress=_P_END, data={"mesh": result},
    )
    return result
