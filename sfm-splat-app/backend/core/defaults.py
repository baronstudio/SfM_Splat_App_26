"""
defaults.py — business defaults per wizard step, persisted in defaults.json.

Layer 2 of the three-layer settings model (CLAUDE.md §4):

    config.json           → installation: exe paths, URLs         (core/config.py)
    defaults.json         → business defaults per step            ← this module
    Project.settings_json → per-project overrides, always win

Pure module: no FastAPI import here, so the pipeline and the tests can read the
defaults without spinning up the API.
"""

import json
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

DEFAULTS_PATH = Path(__file__).parent.parent.parent / "defaults.json"

SCHEMA_VERSION = 1


# ── Capture presets ──────────────────────────────────────────────────────────
# Code-defined and read-only: a preset added in a later version must reach
# existing installs, which it would not if presets lived in defaults.json.
# target_frame_count and the overlap band travel together because they are two
# views of the same physical quantity — how fast the camera moves through space.

class CapturePreset(BaseModel):
    id: str
    label: str
    target_frame_count: int
    min_fps: float
    max_fps: float
    overlap_min_step_pct: float
    overlap_band_max_pct: float
    notes: str = ""


CAPTURE_PRESETS: list[CapturePreset] = [
    CapturePreset(
        id="orbit_drone",
        label="Drone orbit",
        target_frame_count=300,
        min_fps=0.5,
        max_fps=6.0,
        overlap_min_step_pct=2.0,
        overlap_band_max_pct=12.0,
        notes="Smooth continuous orbit around a subject. Constant motion, few cuts.",
    ),
    CapturePreset(
        id="handheld_walk",
        label="Handheld walkthrough",
        target_frame_count=450,
        min_fps=1.0,
        max_fps=10.0,
        overlap_min_step_pct=2.0,
        overlap_band_max_pct=10.0,
        notes="Irregular speed and more motion blur — extract denser, let the blur "
              "filter cut. Tighter overlap band because the path is not smooth.",
    ),
    CapturePreset(
        id="turntable",
        label="Turntable / object",
        target_frame_count=200,
        min_fps=0.5,
        max_fps=5.0,
        overlap_min_step_pct=1.5,
        overlap_band_max_pct=8.0,
        notes="Object rotates, camera fixed. Regular angular step, so a narrow band "
              "is safe and redundant frames are cheap to drop.",
    ),
    CapturePreset(
        id="interior_scan",
        label="Interior scan",
        target_frame_count=600,
        min_fps=1.0,
        max_fps=12.0,
        overlap_min_step_pct=2.5,
        overlap_band_max_pct=9.0,
        notes="Confined space: parallax grows fast, so keep more frames and a "
              "conservative max step to avoid alignment breaks.",
    ),
]

PRESETS_BY_ID: dict[str, CapturePreset] = {p.id: p for p in CAPTURE_PRESETS}


# ── Per-step defaults ────────────────────────────────────────────────────────

FpsMode = Literal["auto", "ratio", "absolute"]


class ExtractDefaults(BaseModel):
    capture_preset: str = "orbit_drone"
    fps_mode: FpsMode = "auto"
    # Fraction of the source cadence. 0.2 is JB's habitual value and the
    # RealityScan video-import default: on a 100 fps rush it yields 20 img/s.
    fps_ratio: float = 0.2
    fps_absolute: float = 2.0
    target_frame_count: int = 300
    # OFF on purpose: mpdecimate duplicates the overlap gate AND drops frames
    # non-deterministically, which breaks the frame-index ↔ timecode mapping that
    # scene detection and the timeline rely on. See CLAUDE.md §6.1.
    mpdecimate: bool = False
    # -qscale:v, the mjpeg quantiser: compression only, never pixel dimensions.
    quality: int = 2
    # Percentage of the source resolution written to disk. 100 adds no scale
    # filter at all, so the default extraction is bit-for-bit what it was.
    scale_percent: int = Field(default=100, ge=10, le=100)
    max_frames: int = 0
    # Imported image sets only (§6.7). When the set is PNG with a real alpha
    # channel, step 2 writes RGBA frames *and* extracts the channel into
    # `masks/` as one image per frame. Both copies exist for LichtFeld Studio,
    # which reads either; RealityScan has no alpha concept for source images
    # and is only the thing in between. A set with no alpha ignores this, and a
    # video can never produce one — FFmpeg writes mjpeg.
    #
    # Default on: a set that went to the trouble of carrying transparency
    # carries it for a reason, and the step says what it did either way.
    keep_alpha: bool = True


class CurateDefaults(BaseModel):
    enabled: bool = True
    auto_after_extract: bool = True
    scene_detector: Literal["adaptive", "content", "off"] = "adaptive"
    # Where the cuts come from. "auto" prefers the scdet scores the extraction
    # captured on frames it was decoding anyway, which is what removes
    # PySceneDetect's second decode of the source (§15.4) — it falls back to
    # PySceneDetect on its own whenever those scores are missing or truncated.
    # "video" pins PySceneDetect, which is the reference "auto" was measured
    # against; "frames" pins the histogram fallback over the extracted frames.
    cut_source: Literal["auto", "video", "frames"] = "auto"
    min_scene_len: int = 15
    sharpness_window: int = 15
    # 0-100. The fraction of the local sharpness median a frame must reach:
    # 0 rejects nothing, 50 rejects anything under half the median.
    sharpness_sensitivity: int = 50
    # When true the band below is taken from the active capture preset, which is
    # where it belongs (§6.2: the preset describes how fast the camera travels).
    # Turn it off to pin the band by hand for one project.
    overlap_from_preset: bool = True
    overlap_min_step_pct: float = 2.0
    overlap_band_max_pct: float = 12.0


class SfmDefaults(BaseModel):
    """Step 3 — `spirula sfm auto` (CLAUDE.md §7.1).

    Every default here is the one the installed build prints, not a value of our
    own: `docs/spirula/sfm-auto-help.txt` is the record, and a flag is only
    modelled when it appears there. `sfm auto` deliberately exposes two headline
    knobs — `--quality` and `--data-type` — and reports what they moved; the rest
    stay at the build's own defaults until a measurement says otherwise.
    """
    # The build's default is `high`. `medium` reconstructed 251/251 images at
    # 0.50 px mean reprojection in 34.6 s on this workstation (§7.1), so `high`
    # stays the default and `medium` is the knob to reach for.
    quality: Literal["low", "medium", "high", "extreme"] = "high"
    # `video` switches pair selection to sequential + loop closure, which is
    # what a project that came through step 2's frame extraction is.
    data_type: Literal["individual", "video", "internet"] = "individual"
    # `auto` is GPU pair selection at 100 images or more, sequential (plus loop
    # closure) for video below that, and exhaustive otherwise.
    pairs: Literal["auto", "exhaustive", "sequential", "prefilter"] = "auto"
    # 360 / fisheye capture is a first-class input (§1): spirula reads
    # equirectangular and >180 deg fisheye natively, with no undistortion pass,
    # so the lens model is a setting rather than a repair.
    camera_model: Literal[
        "simple-pinhole", "pinhole", "radial", "opencv", "full-opencv",
        "opencv-fisheye", "thin-prism-fisheye", "equirectangular",
    ] = "opencv"
    camera_mode: Literal["single", "folder", "image"] = "folder"
    # 0 lets the frontend pick: 3200 for sift, 1600 for aliked.
    max_image_size: int = 0
    max_features: int = 8192
    # Masks are adopted from a `masks/` sibling of the image directory without
    # being named. This is the switch that refuses them (`--no-masks`).
    use_masks: bool = True
    # `--progress-dir` writes model.bin + pairs.bin snapshots for a front end
    # that wants to *show* a run rather than tail it. Off until the viewer can
    # read them; the stdout channel is what step 3's bar is built on (§15).
    progress_dir: bool = False


class SamDefaults(BaseModel):
    """Masking — `spirula sam` (CLAUDE.md §7.4).

    Two routes with very different costs, which is why they are one block with a
    mode rather than two features. `shape` needs no model and no download: it
    masks what is never scene in any frame — a fisheye border, a watermark, the
    rig in shot — and is the companion of the 360 / fisheye input of §1. `track`
    runs SAM over the frames and needs a checkpoint, whose licence is a row in
    the audit table (§10) and is shown before the first fetch.
    """
    mode: Literal["off", "shape", "track"] = "off"

    # -- shape: no model, no download --
    # A leading '-' cuts a shape out again; ';' separates them. Empty means
    # "look for the border yourself", which is what the tool does by default.
    shape_spec: str = ""
    # Fraction of its radius the found boundary is pulled inwards. The outermost
    # pixels of a lens circle are dim and smeared, and worth losing.
    shrink: float = 0.01
    samples: int = 24
    dark: int = 16
    # Without --replace the masks are *intersected* with what is already in the
    # output folder, which is how the shape pass stacks on top of a model's.
    replace: bool = False

    # -- track: needs a SAM checkpoint --
    # `sam track --model` takes a **file**, not an id: unlike `geometry`, which
    # fetches a known id into its own cache, there is no download on this route
    # at all. The checkpoints are never bundled (§10), so this is the path of
    # one the user fetched by hand.
    model: str = ""
    # Which licence was read and accepted for that file. Two rows in §10 and two
    # separate acceptances, because they are not the same question: SAM 2.1 is
    # Apache-2.0 and SAM 3 is Meta's own, non-standard licence. Empty refuses the
    # run — an unaccepted licence is not a default we may pick on JB's behalf.
    model_licence: Literal["", "sam2.1", "sam3"] = ""
    text: str = ""
    neg_text: str = ""
    detect_every: int = 1
    threshold: float = 0.5
    nms: float = 0.1
    max_size: int = 1600
    # The tool's own default is that the prompted objects are BLACK and
    # everything else white — "which is what a reconstruction pipeline wants
    # from 'mask out the people'". `--keep-prompted` inverts it, for a prompt
    # that names the subject instead of the distractors.
    keep_prompted: bool = False


class GeometryDefaults(BaseModel):
    """Depth and normal maps — `spirula geometry` (CLAUDE.md §7.5).

    A panel on step 4, not a step of its own: it writes `normals/` and `depths/`
    beside the images, which both dataset readers find by name, and nothing
    rewrites the reconstruction. Depth is OFF in the tool's own default — the
    normals are what a reconstruction usually wants, and depth doubles both the
    disk and the reading a training run does.
    """
    enabled: bool = False
    # The build's own default when --model is not given, measured 2026-08-27:
    # it fetches `moge2-vitb-normal.onnx`, 419.4 MB, from HuggingFace into
    # %LOCALAPPDATA%\spirula-studio\models\. Empty means "let it choose".
    model: str = ""
    depth: bool = False
    max_size: int = 1064
    normal_format: Literal["png", "jpg"] = "jpg"
    jpeg_quality: int = 95
    depth_units: Literal["relative", "mm"] = "relative"
    # `auto` picks ray depth exactly when the frame was split into pinhole
    # faces, which is the same call the trainer's --input-depth-is-ray-depth
    # makes when it is left unset.
    ray_depth: Literal["auto", "yes", "no"] = "auto"
    # `auto` splits a panorama always and a fisheye when one pinhole would keep
    # less than three quarters of the frame — the 360 input of §1 again.
    split: Literal["auto", "yes", "no"] = "auto"
    overwrite: bool = False


class TrainDefaults(BaseModel):
    """Step 4 — `spirula train` (CLAUDE.md §7.6).

    Read off `docs/spirula/train-help-all-*.txt`, one capture per preset, and
    only flags that build actually has.

    **`None` means "the preset decides", and every tool knob here defaults to
    it.** The preset is the first positional argument and it moves the defaults
    of everything under it — `meshing` alone sets `--primitive 3dgut`,
    `--sh-degree 0` and `--background-mode noise` — so a model holding concrete
    numbers could not tell "the user asked for 3" from "3 is what `3dgs`
    happened to default to", and switching the preset would send the previous
    one's whole block back on the command line and silently undo the new one.
    Naming a flag overrides the preset, which is the same rule `SfmDefaults`
    follows against the build's own defaults (§12, 2026-08-27); here the
    baseline is per-preset rather than global, so it cannot be a literal in this
    file. `step_train.preset_defaults()` is that table, and it is what the panel
    shows for an unset knob.

    The four `load_*` / `apply_*` switches are the exception: they are the
    user's *intent* ("use the masks if there are any"), not a value handed
    straight to the tool, and the run resolves each of them against what is
    actually on disk before deciding whether a flag is needed at all.
    """
    # `train --help` lists six presets; `academic-baseline` is a seventh that
    # works and is not listed — measured 2026-08-27, see
    # docs/spirula/train-help-all-academic-baseline.txt.
    preset: Literal[
        "3dgs", "360-camera", "in-the-wild", "linear-color", "synthetic",
        "meshing", "academic-baseline",
    ] = "3dgs"

    # -- run length, splat budget and model shape --
    num_iterations: Optional[int] = None
    quality: Optional[Literal["low", "medium", "high", "ultra"]] = None
    cap_max: Optional[int] = None
    sh_degree: Optional[int] = None
    primitive: Optional[Literal["3dgs", "mip", "3dgut"]] = None
    background_mode: Optional[Literal["black", "noise", "sh"]] = None
    steps_per_save: Optional[int] = None
    # The build keeps only the newest checkpoint, so exactly one survives a run.
    save_only_latest_checkpoint: Optional[bool] = None
    save_eval_images: Optional[bool] = None
    distraction_robustness: Optional[Literal["off", "mild", "strong"]] = None
    floater_suppression: Optional[Literal["off", "mild", "strong"]] = None

    # -- masks --
    # Intent, not a flag: the run sends `--load-masks 0` when this is off or
    # `masks/` is empty, and points `--mask-dir` at the absolute path otherwise.
    load_masks: bool = True
    # The trainer's own default for apply_loss_for_mask is 0, and 0 means
    # *ignore*: its help reads "Off ignores them... On trains them as empty,
    # which removes the background and leaves just the subject." That is the
    # `ignore` / `segment` pair 3DGS_App_26 measured on 2026-08-26, where
    # `ignore` was indistinguishable from no masks at all — 79.0 % of gaussians
    # inside the region box against 79.3 % unmasked, p99 radius 147.0 against
    # 149.6 — and `segment` was the whole effect, 96.3 % and a p99 radius of
    # 19.5. So the masked route sends 1, and the off position is not offered
    # under it: this ships True and the UI has no switch for it.
    apply_loss_for_mask: bool = True
    # Signed: grows or shrinks the masks by this fraction of the image size.
    # No LichtFeld Studio equivalent. `360-camera` and `in-the-wild` preset it
    # to -0.025, which is exactly why it is None here rather than 0.0.
    mask_boundary_offset: Optional[float] = None

    # -- geometry supervision, fed by GeometryDefaults --
    # Intent again: the run sends 0 when `sfm/depths` or `sfm/normals` is empty,
    # and otherwise lets `--depth-dir` / `--normal-dir` keep their relative
    # defaults, which resolve inside `--data` at no flag cost (§7.5).
    load_depths: bool = True
    load_normals: bool = True
    depth_supervision_weight: Optional[float] = None
    normal_supervision_weight: Optional[float] = None

    # -- scene placement --
    orientation_method: Optional[
        Literal["pca", "up", "vertical", "none", "gsplat"]
    ] = None
    center_method: Optional[Literal["poses", "focus", "none", "gsplat"]] = None
    auto_scale_poses: Optional[bool] = None
    train_frame: Optional[Literal["normalized", "camera", "points"]] = None


class MeshDefaults(BaseModel):
    """Step 5 — `spirula mesh` (CLAUDE.md §7.7).

    The checkpoint is step 4's; `--data` gives the cameras that decide occupancy
    and colour. Two combinations the tool refuses outright and the UI must not
    offer: PLY with a texture, and OBJ with vertex colours.
    """
    formats: list[Literal["ply", "obj", "gltf", "glb"]] = Field(
        default_factory=lambda: ["glb"]
    )
    color: Literal["none", "vertex", "texture"] = "vertex"
    # With --color texture a format may carry a texture encoding: glb+png is the
    # build's default, glb+jpg is q95, glb+jpeg75 is q75.
    texture_encoding: Literal["png", "jpg", "jpeg75"] = "png"
    # 0 takes it from the observed-detail texel budget.
    texture_size: int = 0
    # The build's own default depends on whether cameras are used: 0.5 with,
    # 0.2 without. 0 means "let the build decide", the same convention
    # `texture_size = 0` and `max_cameras = -1` use here.
    iso: float = 0.0
    use_cameras: bool = True
    max_cameras: int = -1
    max_grid_res: int = 512
    cull_unseen: bool = True
    floater_min_faces: int = 100
    quality_iters: int = 3
    num_threads: int = 0


class ExportDefaults(BaseModel):
    """The deliverable copy of a trained splat (CLAUDE.md §7.6c).

    Every field bar `format` is a *reduction*, and every one of them ships off:
    the default export is the trained splat byte for byte, in the trainer's own
    format. "Give me the file" has to be one obvious setting rather than a
    combination, and each knob below is then a deliberate act with a measured
    cost printed next to it in the panel.

    `opacity_min` in particular ships **0** on a measurement rather than on
    caution. Spirula's gaussians are low-opacity by construction — median linear
    alpha 0.059 under `--opacity-reg 0.01` against a 1 M cap — so the 1/255 floor
    every other 3DGS toolchain ships as free housekeeping drops 1.2 % of the
    reference file, and anything high enough to matter (43.2 % at 0.05) is an
    edit of the picture, not a cleanup.
    """

    #: `ply` and `splat` are written here; `sog`, `spz` and `compressed-ply`
    #: need `@playcanvas/splat-transform` (§10) and say so when it is absent.
    format: Literal["ply", "splat", "sog", "spz", "compressed-ply"] = "ply"
    #: Highest SH band to keep. None keeps whatever the trainer wrote; 0 drops
    #: all 45 `f_rest_*` and 72.6 % of every vertex with them.
    sh_degree: Optional[int] = None
    #: Linear alpha floor, after the sigmoid. 0 keeps every gaussian.
    opacity_min: float = 0.0
    #: Target gaussian count. 0 keeps every one that survives the floor.
    max_count: int = 0
    #: How `max_count` chooses: `importance` is alpha x ellipsoid volume,
    #: `uniform` is an even spread over the file.
    selection: Literal["importance", "uniform"] = "importance"
    #: Inherited, and left alone: step 5's `export/` naming, not this pass's.
    pattern: str = "{project}_{index:05d}"


class ViewerDefaults(BaseModel):
    """The 3D preview in steps 3, 4 and 5.

    `preview_max_points` is what the viewer opens at, not a ceiling: the "full
    quality" button asks for the whole file. It exists because the LFS splat is
    measured in gigabytes and the first thing you want is a picture, not a
    perfect picture. 0 opens at full quality.
    """
    preview_max_points: int = 1_000_000
    point_size: float = 1.6
    show_cameras: bool = True
    show_camera_path: bool = True
    background: str = "#0b1220"


class AppDefaults(BaseModel):
    schema_version: int = SCHEMA_VERSION
    extract: ExtractDefaults = Field(default_factory=ExtractDefaults)
    curate: CurateDefaults = Field(default_factory=CurateDefaults)
    sfm: SfmDefaults = Field(default_factory=SfmDefaults)
    sam: SamDefaults = Field(default_factory=SamDefaults)
    geometry: GeometryDefaults = Field(default_factory=GeometryDefaults)
    train: TrainDefaults = Field(default_factory=TrainDefaults)
    mesh: MeshDefaults = Field(default_factory=MeshDefaults)
    export: ExportDefaults = Field(default_factory=ExportDefaults)
    viewer: ViewerDefaults = Field(default_factory=ViewerDefaults)


SECTIONS = (
    "extract", "curate", "sfm", "sam", "geometry", "train", "mesh",
    "export", "viewer",
)


# ── Load / save ──────────────────────────────────────────────────────────────

def deep_merge(base: dict, patch: dict) -> dict:
    """Merge patch into base recursively. Patch values win; base keys survive."""
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_defaults() -> AppDefaults:
    """Read defaults.json, creating it from the code defaults if absent.

    Unknown keys are ignored and missing keys fall back to the model defaults,
    so an older defaults.json keeps working after a new field is added.
    """
    if not DEFAULTS_PATH.exists():
        fresh = AppDefaults()
        _write(fresh)
        return fresh
    try:
        with open(DEFAULTS_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        # A corrupt file must not brick the app — fall back to code defaults.
        return AppDefaults()
    return AppDefaults.model_validate(deep_merge(AppDefaults().model_dump(), raw))


def _write(defaults: AppDefaults) -> None:
    with open(DEFAULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(defaults.model_dump(), f, indent=4)


def save_defaults(patch: dict[str, Any]) -> AppDefaults:
    """Deep-merge a partial payload over the stored defaults and persist."""
    current = load_defaults().model_dump()
    merged = AppDefaults.model_validate(deep_merge(current, patch))
    merged.schema_version = SCHEMA_VERSION
    _write(merged)
    return reload_defaults()


def reset_defaults(section: Optional[str] = None) -> AppDefaults:
    """Factory-reset every section, or a single one when `section` is given."""
    if section is None:
        fresh = AppDefaults()
        _write(fresh)
        return reload_defaults()
    if section not in SECTIONS:
        raise ValueError(f"Unknown section '{section}'. Expected one of {SECTIONS}.")
    current = load_defaults().model_dump()
    current[section] = getattr(AppDefaults(), section).model_dump()
    _write(AppDefaults.model_validate(current))
    return reload_defaults()


def reload_defaults() -> AppDefaults:
    """Reload from disk and refresh the module-level singleton."""
    global app_defaults
    app_defaults = load_defaults()
    return app_defaults


# ── Working fps resolution ───────────────────────────────────────────────────

def resolve_extract_fps(
    extract: ExtractDefaults,
    source_fps: Optional[float] = None,
    duration_s: Optional[float] = None,
) -> tuple[float, str]:
    """Resolve the FFmpeg working fps from the policy and the probed source.

    Returns (fps, explanation). The explanation is logged and shown in the UI so
    the number is never a black box.
    """
    preset = PRESETS_BY_ID.get(extract.capture_preset)

    def by_ratio(reason: str = "") -> tuple[float, str]:
        if source_fps and source_fps > 0:
            fps = round(extract.fps_ratio * source_fps, 3)
            return fps, f"{reason}ratio {extract.fps_ratio} x {source_fps:g} fps source = {fps:g} fps"
        return (
            extract.fps_absolute,
            f"{reason}source cadence unknown - falling back to {extract.fps_absolute:g} fps",
        )

    if extract.fps_mode == "absolute":
        return extract.fps_absolute, f"fixed at {extract.fps_absolute:g} fps"

    if extract.fps_mode == "ratio":
        return by_ratio()

    # auto: aim for the preset's target frame count over the real duration.
    if not duration_s or duration_s <= 0:
        return by_ratio("duration unknown - ")

    target = extract.target_frame_count or (preset.target_frame_count if preset else 300)
    fps = target / duration_s

    lo = preset.min_fps if preset else 0.1
    hi = preset.max_fps if preset else 30.0
    clamped = min(max(fps, lo), hi)
    # Never ask FFmpeg for more frames than the source actually holds.
    if source_fps and source_fps > 0:
        clamped = min(clamped, source_fps)

    fps_r = round(clamped, 3)
    note = "" if abs(clamped - fps) < 1e-6 else f" (clamped from {fps:.3g})"
    label = preset.label if preset else "no preset"
    return fps_r, f"auto: {target} frames over {duration_s:.1f}s = {fps_r:g} fps{note} [{label}]"


app_defaults: AppDefaults = load_defaults()
