"""
step_analyze.py — the curation phase of wizard step 2 (CLAUDE.md §6.3).

Runs automatically after extraction and is independently re-runnable: thresholds
are tuned iteratively and re-extracting frames to change one number is
unacceptable, so nothing here reads the source video except the cut detector,
and even that degrades to a frames-only fallback.

This module owns the file I/O and the broadcasting; the measurements themselves
live in `core/curate/`, which imports neither FastAPI nor this file.
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

from backend.core import frames as frame_files
from backend.core.curate import overlap, scenes, select, sharpness
from backend.core.defaults import PRESETS_BY_ID, CurateDefaults, load_defaults

# Frames handed to the thread pool per executor round-trip. Small enough that an
# abort is observed within a second or so, large enough that the hand-off cost
# stays negligible next to the OpenCV work.
CHUNK = 24

# Re-exported from core.frames, which is now the one definition — a mask
# sidecar is a .png in the same directory and must never be scored as a frame.
FRAME_SUFFIXES = frame_files.FRAME_SUFFIXES


class AnalysisAborted(RuntimeError):
    """Raised when the user aborts mid-analysis. Not an error condition."""


# ── Settings resolution ──────────────────────────────────────────────────────

def resolve_curate_settings(settings: dict) -> tuple[CurateDefaults, str]:
    """Overlay the per-project settings onto the app defaults (CLAUDE.md §4).

    When `overlap_from_preset` is on, the band comes from the capture preset the
    project extracts with — the preset carries the target frame count and the
    overlap band together because they are two views of the same thing (§6.2).

    Returns (resolved, band_explanation).
    """
    defaults = load_defaults()
    base = defaults.curate.model_dump()
    incoming = settings or {}

    # A project may send the curate block nested or flat; accept both.
    nested = incoming.get("curate")
    patch_source = nested if isinstance(nested, dict) else incoming
    patch = {k: v for k, v in patch_source.items() if k in base and v is not None}
    resolved = CurateDefaults.model_validate({**base, **patch})

    # The preset lives in the extract block, which now arrives nested — reading
    # only the top level would silently band every project on the *default*
    # preset the moment step 2 stopped sending its settings flat.
    extract_block = incoming.get("extract")
    preset_id = (
        (extract_block.get("capture_preset") if isinstance(extract_block, dict) else None)
        or incoming.get("capture_preset")
        or defaults.extract.capture_preset
    )
    preset = PRESETS_BY_ID.get(preset_id)

    if resolved.overlap_from_preset and preset is not None:
        resolved = resolved.model_copy(update={
            "overlap_min_step_pct": preset.overlap_min_step_pct,
            "overlap_band_max_pct": preset.overlap_band_max_pct,
        })
        source = f"from preset '{preset.label}'"
    else:
        source = "set manually"

    explanation = (
        f"band {resolved.overlap_min_step_pct:g}-"
        f"{resolved.overlap_band_max_pct:g}% {source}"
    )
    return resolved, explanation


# ── analysis/ helpers ────────────────────────────────────────────────────────

def analysis_dir(project_path: Path) -> Path:
    d = project_path / "analysis"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_frames(project_path: Path) -> list[Path]:
    return frame_files.list_frames(project_path / "frames")


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def load_overrides(project_path: Path) -> dict:
    """Manual keep/drop decisions. Never regenerated, always wins (§5)."""
    data = read_json(analysis_dir(project_path) / "overrides.json") or {}
    frames = data.get("frames", data)
    if not isinstance(frames, dict):
        return {}
    return {k: v for k, v in frames.items() if v in (select.KEEP, select.DROP)}


def save_overrides(project_path: Path, overrides: dict) -> dict:
    _write_json(
        analysis_dir(project_path) / "overrides.json",
        {"updated_at": datetime.now(timezone.utc).isoformat(), "frames": overrides},
    )
    return overrides


def rebuild_selection(project_path: Path) -> Optional[dict]:
    """Re-derive selection.json from the existing scores plus the overrides.

    Cheap by construction — no image is opened — which is what makes flipping a
    single frame in the UI instant instead of a full re-analysis.
    """
    scores = read_json(analysis_dir(project_path) / "scores.json")
    if not scores or "frames" not in scores:
        return None
    selection = select.build_selection(scores["frames"], load_overrides(project_path))
    _write_json(analysis_dir(project_path) / "selection.json", selection)
    return selection


# ── Chunked, abort-aware execution ───────────────────────────────────────────

def _check_abort(should_abort: Optional[Callable[[], bool]]) -> None:
    if should_abort is not None and should_abort():
        raise AnalysisAborted("Analysis aborted by user")


async def _chunked(
    paths: Sequence[Path],
    worker: Callable[[list], list],
    broadcast_fn,
    lo: float,
    hi: float,
    should_abort: Optional[Callable[[], bool]],
) -> list:
    """Map `worker` over the frames in the thread pool, honouring abort/progress."""
    loop = asyncio.get_running_loop()
    total = len(paths)
    out: list = []
    for start in range(0, total, CHUNK):
        _check_abort(should_abort)
        piece = list(paths[start:start + CHUNK])
        out.extend(await loop.run_in_executor(None, worker, piece))
        # Empty message: a progress tick, not a log line — analysing a
        # 2000-frame set would otherwise bury every other line in the LiveLog.
        await broadcast_fn(
            "curate", "INFO", "", progress=lo + (hi - lo) * len(out) / total
        )
    return out


# ── Main entry point ─────────────────────────────────────────────────────────

async def run_analysis(
    project_path: Path,
    broadcast_fn,
    settings: dict,
    should_abort: Optional[Callable[[], bool]] = None,
) -> dict:
    """Score, gate and select the extracted frames. Writes analysis/*.json."""
    adir = analysis_dir(project_path)
    frames = list_frames(project_path)
    filenames = [f.name for f in frames]

    await broadcast_fn(
        "curate", "INFO",
        f"[curate] Analysing {len(frames)} frames...",
        status="running",
    )

    if not frames:
        raise FileNotFoundError(
            f"No frames to analyse in {project_path / 'frames'} — run the extraction first."
        )

    curate, band_note = resolve_curate_settings(settings)

    if not curate.enabled:
        selection = select.pass_through_selection(filenames)
        _write_json(adir / "selection.json", selection)
        await broadcast_fn(
            "curate", "WARNING",
            f"[curate] Curation disabled — all {len(frames)} frames kept.",
            progress=1.0,
        )
        return {"selection": selection, "curation": "disabled"}

    extract_meta = read_json(adir / "extract.json") or {}
    working_fps = extract_meta.get("working_fps")
    video_path = Path(extract_meta["input_video"]) if extract_meta.get("input_video") else None
    # Captured by the extraction on frames FFmpeg was decoding anyway. Absent on
    # anything extracted before this existed, and on an mpdecimate run — both of
    # which land on the PySceneDetect path below, exactly as they used to.
    scene_scores = read_json(adir / "scene_scores.json")
    if extract_meta.get("mpdecimate"):
        # The frame index no longer maps to a timecode, so cuts detected on the
        # source video cannot be placed. Force the frames-only fallback.
        working_fps = None
        await broadcast_fn(
            "curate", "WARNING",
            "[curate] mpdecimate was on during extraction — falling back to "
            "frame-based cut detection (frame indices no longer map to timecodes).",
        )

    loop = asyncio.get_running_loop()

    # ── 1. Scenes ────────────────────────────────────────────────────────────
    _check_abort(should_abort)
    await broadcast_fn("curate", "INFO", "[curate] 1/4 Detecting cuts...", progress=0.02)
    sequence_ids, method = await loop.run_in_executor(
        None,
        lambda: scenes.detect_sequences(
            frames, video_path, working_fps, curate.scene_detector, curate.min_scene_len,
            scene_scores=scene_scores, cut_source=curate.cut_source,
        ),
    )
    spans = scenes.sequence_spans(sequence_ids)
    await broadcast_fn(
        "curate", "INFO",
        f"[curate] {len(spans)} sequence(s) — {method}",
        progress=0.25,
    )

    # ── 2. Sharpness ─────────────────────────────────────────────────────────
    await broadcast_fn("curate", "INFO", "[curate] 2/4 Scoring sharpness (Tenengrad)...")
    scores_list = await _chunked(
        frames, sharpness.score_frames, broadcast_fn, 0.25, 0.60, should_abort,
    )
    medians = sharpness.rolling_median(scores_list, sequence_ids, curate.sharpness_window)
    blur = sharpness.blur_flags(scores_list, medians, curate.sharpness_sensitivity)
    await broadcast_fn(
        "curate", "INFO",
        f"[curate] {sum(blur)} frame(s) below {curate.sharpness_sensitivity}% of the "
        f"rolling median over a {curate.sharpness_window}-frame window -> blur",
        progress=0.60,
    )

    # ── 3. Overlap gate ──────────────────────────────────────────────────────
    await broadcast_fn("curate", "INFO", f"[curate] 3/4 Overlap gate — {band_note}")
    features = await _chunked(
        frames, overlap.extract_all, broadcast_fn, 0.60, 0.92, should_abort,
    )
    _check_abort(should_abort)
    displacements, redundant, gap = await loop.run_in_executor(
        None,
        lambda: overlap.gate(
            features, sequence_ids, blur,
            curate.overlap_min_step_pct, curate.overlap_band_max_pct,
        ),
    )
    await broadcast_fn(
        "curate", "INFO",
        f"[curate] {sum(redundant)} redundant, {sum(gap)} gap warning(s)",
        progress=0.94,
    )

    # ── 4. Select ────────────────────────────────────────────────────────────
    _check_abort(should_abort)
    records = select.build_scores(
        filenames, sequence_ids, scores_list, medians, displacements, blur, redundant, gap
    )
    kept_flags = [r["auto_verdict"] == "kept" for r in records]

    scores_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "params": {
            **curate.model_dump(),
            "scene_method": method,
            "band_source": band_note,
            "working_fps": working_fps,
        },
        "sequences": spans,
        "stats": {
            "sharpness_all": sharpness.summarise(scores_list),
            "sharpness_kept": sharpness.summarise(
                [s for s, k in zip(scores_list, kept_flags) if k]
            ),
            "overlap": overlap.band_quality(
                displacements, kept_flags,
                curate.overlap_min_step_pct, curate.overlap_band_max_pct,
            ),
        },
        "frames": records,
    }
    _write_json(adir / "scores.json", scores_payload)

    overrides = load_overrides(project_path)
    if not (adir / "overrides.json").exists():
        save_overrides(project_path, {})
    selection = select.build_selection(records, overrides)
    _write_json(adir / "selection.json", selection)

    summary = selection["summary"]
    await broadcast_fn(
        "curate", "SUCCESS",
        f"[curate] {summary['kept']}/{summary['total']} frames kept "
        f"({summary['removed_pct']:g}% removed: {summary['rejected_blur']} blur, "
        f"{summary['rejected_redundant']} redundant, {summary['rejected_manual']} manual)",
        progress=1.0,
    )
    return {"selection": selection, "scores_path": str(adir / "scores.json")}
