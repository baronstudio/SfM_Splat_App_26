export interface Project {
  id: string;
  name: string;
  slug: string;
  created_at: string;
  updated_at: string;
  current_step: number;
  step_status: Record<string, string>;
  input_video_path: string | null;
  frame_count: number;
  settings_json: string;
  error_message: string | null;
  thumbnail_url: string | null;
  /** Absolute path of projects/<slug>/ on this machine — shown on the tile. */
  path: string;
  /** Archived: the files are zipped away and the project is read-only. */
  archived: boolean;
  archived_at: string | null;
  archive_path: string | null;
}

/**
 * A project-level file operation in flight (copy / reset / archive / restore).
 * It lives in the store, not in the list component, because the list unmounts
 * as soon as the user changes step — and the progress view must not go with it.
 */
export interface ProjectOperation {
  projectId: string;
  /** Imperative title of the modal: "Copying project". */
  title: string;
  projectName: string;
  /** 0 → 1, from the WS bus. */
  progress: number;
  message: string;
  /** Set when the operation failed; the modal then waits to be dismissed. */
  error: string | null;
}

/** Wizard steps a reset can wipe. Step 1 (import) owns the source video and is
 *  never reset — that is the whole point of the option. */
export const RESETTABLE_STEPS = [2, 3, 4, 5, 6] as const;

// More names than there are wizard steps, because three of them are phases
// reporting into a step rather than steps of their own (CLAUDE.md §6, §7.4,
// §7.5): 'curate' is step 2's second phase, 'masks' is the `spirula sam` run
// that attaches to step 3, and 'geometry' is the `spirula geometry` run that
// attaches to step 4. Each is separately re-runnable — the expensive phase must
// never be redone to change a threshold — which is exactly why it needs its own
// progress name.
export type StepName =
  | 'extract' | 'curate' | 'sfm' | 'masks'
  | 'geometry' | 'train' | 'mesh' | 'export' | 'scene';
export type StepStatus = 'pending' | 'running' | 'done' | 'error' | 'aborted';
export type LogLevel = 'INFO' | 'WARNING' | 'ERROR' | 'SUCCESS' | 'DEBUG';

export interface WsMessage {
  type: 'log' | 'progress' | 'metric' | 'status' | 'file_ready';
  step: string;
  timestamp: string;
  level?: string;
  message?: string;
  progress?: number;
  status?: string;
  data?: {
    iteration?: number;
    total_iterations?: number;
    loss?: number;
    psnr?: number;
    /** `spirula train` reports structural similarity on every bar line. */
    ssim?: number;
    num_gaussians?: number;
    iter_per_sec?: number;
    elapsed_s?: number;
    /** Derived from the real step rate, and sensible from step 1 (§7.7). */
    eta_s?: number;
  };
  file?: string;
}

export interface LogEntry {
  id: string;
  timestamp: string;
  step: string;
  level: LogLevel;
  message: string;
}

// One point of the training chart, folded from the bar line `spirula train`
// prints every hundred steps (CLAUDE.md §7.7). Every field is optional because
// a build may drop one, never because zero is missing: `ssim=0` and `psnr=20`
// both occur, and a pattern that required a decimal point would lose exactly
// the lines a run starts and ends on.
export interface TrainMetric {
  iteration: number;
  loss?: number;
  psnr?: number;
  ssim?: number;
  num_gaussians?: number;
}

export interface ExportFile {
  filename: string;
  url: string;
  size_bytes: number;
}

/** RealityScan's three alignment values, spelled as its own CLI takes them. */
export type RCImageOverlap = 'Low' | 'Medium' | 'High';

export interface RCSettingsType {
  feature_detection_quality: 'Normal' | 'High';
  max_features: number;
  image_overlap: RCImageOverlap;
  keep_largest: boolean;
  merge_components: boolean;
  /** `rc.region` — the seed the step-3 box editor starts from (SESSION 12). */
  region?: RegionDefaults;
  /** `rc.masks` — the mask run offered after the alignment (TODO P4). */
  masks?: MaskGenerationDefaults;
}

// LichtFeld Studio v0.5.3 strategies. 'default' sends no --strategy flag and
// lets the build pick, which is MRNF.
export type LFSStrategy = 'default' | 'mcmc' | 'mrnf' | 'igs+';

// v0.5.3's --mask-mode values. Only reachable when step 2 kept an alpha channel
// and step 3 got it as far as the dataset (§6.7); 'none' sends no flag.
// 'ignore' is not offered: it leaves the masked pixels unsupervised rather than
// removing them, which measures the same as an unmasked run (§7.5). The backend
// reads a stored 'ignore' as 'segment'.
export type MaskMode =
  | 'none'
  | 'segment'
  | 'segment_and_ignore'
  | 'alpha_consistent';

export interface LFSSettingsType {
  iterations: number;
  strategy: LFSStrategy;
  /** --max-cap. 0 sends no flag and leaves the ceiling to the build (2 M in v0.5.3). */
  max_gaussians: number;
  eval: boolean;
  save_eval_images: boolean;
  background_color: string;
  /** --mask-mode. Sent only when the dataset actually carries masks (§6.7). */
  mask_mode: MaskMode;
}

// ── App defaults (defaults.json — layer 2 of the settings model) ─────────────

export type FpsMode = 'auto' | 'ratio' | 'absolute';

export interface CapturePreset {
  id: string;
  label: string;
  target_frame_count: number;
  min_fps: number;
  max_fps: number;
  overlap_min_step_pct: number;
  overlap_band_max_pct: number;
  notes: string;
}

export interface ExtractDefaults {
  capture_preset: string;
  fps_mode: FpsMode;
  fps_ratio: number;
  fps_absolute: number;
  target_frame_count: number;
  mpdecimate: boolean;
  /** FFmpeg -qscale:v — JPEG compression, 1 (best) to 5. Not a resolution. */
  quality: number;
  /** Percentage of the source resolution written to disk. 100 = no downscale. */
  scale_percent: number;
  max_frames: number;
  /** Imported image sets only: keep a PNG alpha channel and write RS masks
   *  from it. A video can never produce one — FFmpeg writes mjpeg. */
  keep_alpha: boolean;
}

export interface CurateDefaults {
  enabled: boolean;
  auto_after_extract: boolean;
  scene_detector: 'adaptive' | 'content' | 'off';
  cut_source: 'auto' | 'video' | 'frames';
  min_scene_len: number;
  sharpness_window: number;
  sharpness_sensitivity: number;
  /** When on, the overlap band comes from the active capture preset (§6.2). */
  overlap_from_preset: boolean;
  overlap_min_step_pct: number;
  overlap_band_max_pct: number;
}

/** RealityScan's "Undistortion settings" block (CLAUDE.md §7.2). */
export interface UndistortDefaults {
  enabled: boolean;
  fit: 'outer_boundary' | 'inner_region' | 'in_between';
  resolution: 'preserve' | 'custom' | 'fit';
  custom_width: number;
  custom_height: number;
  downscale: number;
  undistort_principal_point: boolean;
  image_cutout: number;
  max_pixels: number;
  export_images: boolean;
  image_format: 'png' | 'jpg' | 'tiff';
  pixel_format: string;
  naming_convention: 'sequential' | 'original';
  background_color: string;
}

/** The COLMAP registration export of step 3. */
export interface ColmapExportDefaults {
  enabled: boolean;
  directory_structure: 'standard' | 'flat';
  file_type: 'binary' | 'ascii';
  exclude_unreliable_tie_points: boolean;
  export_masks: boolean;
  mask_extension: 'ext' | 'mask_ext';
  scene_rotate_x_deg: number;
  undistort: UndistortDefaults;
}

/** `rc.region` — the Reconstruction Region asked of RealityScan (SESSION 12). */
export interface RegionDefaults {
  mode: 'off' | 'auto' | 'density';
  /** -scaleReconstructionRegion sx sy sz center factor; all ones emits no verb. */
  scale: number[];
  export: boolean;
}

export interface RCDefaults {
  feature_detection_quality: 'Normal' | 'High';
  /** sfmMaxFeaturesPerMpx — the per-megapixel budget the per-image cap sits on. */
  max_features_per_mpx: number;
  max_features: number;
  image_overlap: RCImageOverlap;
  keep_largest: boolean;
  merge_components: boolean;
  normalise_for_lfs: boolean;
  save_project: boolean;
  colmap: ColmapExportDefaults;
  region: RegionDefaults;
  masks: MaskGenerationDefaults;
  extra_align_commands: string[];
}

/** `rc.masks` — the mask run of step 3 (TODO P4).
 *
 *  Not part of the alignment: a separate RealityScan process over the saved
 *  `.rsproj`, which meshes inside the validated Reconstruction Region and
 *  re-exports the COLMAP dataset with the mask layers in it.
 */
export interface MaskGenerationDefaults {
  enabled: boolean;
  mesh_quality: 'preview' | 'normal' | 'high';
  use_region: boolean;
  save_project_after: boolean;
  preview_downscale: number;
  normal_downscale: number;
  gpu_acceleration: boolean;
}

/** What the mask run reports back — `rc_alpha.fit_dataset_masks`. */
export interface MaskReport {
  masks: number;
  images: number;
  matched: number;
  resized: number;
  unmatched: string[];
  size: number[] | null;
  state: 'none' | 'ready' | 'partial' | 'unusable';
  note: string | null;
}

/** An oriented box, in the frame it says it is in — `region/region.json`.
 *
 *  `euler_deg` is `(rx, ry, rz)` in degrees applied as `Rz·Ry·Rx`, i.e.
 *  `THREE.Euler(..., 'ZYX')`. It is **not** RealityScan's own `yawPitchRoll`,
 *  which is a different triple in a different frame; that one is kept under
 *  `rsbox` for the round-trip and is nothing the UI reads.
 */
export interface Region {
  frame: 'nerf' | 'rc';
  centre: number[];
  size: number[];
  euler_deg: number[];
  source: 'rsbox_auto' | 'manual' | 'pointcloud_percentile' | string;
  euler_order?: string;
  coverage?: number | null;
  points_inside?: number | null;
  points_total?: number | null;
  updated_at?: string;
  rsbox?: Record<string, unknown>;
}

export interface RegionState {
  region: Region | null;
  /** Where `region` came from when nothing is saved yet. */
  source: string;
  /** True when this is a seed, not something the user validated. */
  seeded: boolean;
  saved: boolean;
  rsbox: boolean;
  auto_rsbox: boolean;
  has_cloud: boolean;
  /** Which frame `pointcloud.ply` — and therefore the preview — is in. */
  cloud_frame: 'nerf' | 'rc';
  removed?: string[];
}

/** Coverage of the last alignment — rc_output/alignment_check.json (§7). */
export interface AlignmentSequenceStat {
  sequence_id: number;
  input: number;
  aligned: number;
  missing: number;
}

export interface AlignmentReport {
  checked: boolean;
  reason: string | null;
  input_count: number;
  aligned_count: number;
  missing_count: number;
  aligned_ratio: number | null;
  single_component: boolean | null;
  missing_frames: string[];
  sequences: AlignmentSequenceStat[];
  source: string | null;
}

export interface LFSDefaults {
  iterations: number;
  strategy: LFSStrategy;
  max_gaussians: number;
  eval: boolean;
  save_eval_images: boolean;
  background_color: string;
  /** `--mask-mode`, sent only when the dataset actually has masks. */
  mask_mode: MaskMode;
}

export interface ExportDefaults {
  format: 'ply' | 'splat';
  pattern: string;
}

export interface BlenderDefaults {
  scene_scale: number;
  import_mode: string;
}

export interface ViewerDefaults {
  /** What the 3D preview opens at. 0 opens at full quality. */
  preview_max_points: number;
  point_size: number;
  show_cameras: boolean;
  show_camera_path: boolean;
  background: string;
}

// ── Step 3 — `spirula sfm auto` (CLAUDE.md §7.1) ─────────────────────────────

/** The lens models `--camera-model` takes. The last three are why 360 capture
 *  needs no undistortion pass anywhere in this pipeline (§1). */
export type SfmCameraModel =
  | 'simple-pinhole' | 'pinhole' | 'radial' | 'opencv' | 'full-opencv'
  | 'opencv-fisheye' | 'thin-prism-fisheye' | 'equirectangular';

export interface SfmDefaults {
  quality: 'low' | 'medium' | 'high' | 'extreme';
  data_type: 'individual' | 'video' | 'internet';
  pairs: 'auto' | 'exhaustive' | 'sequential' | 'prefilter';
  camera_model: SfmCameraModel;
  camera_mode: 'single' | 'folder' | 'image';
  /** 0 lets the frontend pick: 3200 for sift, 1600 for aliked. */
  max_image_size: number;
  max_features: number;
  /** Off sends `--no-masks`. Adoption of a `masks/` sibling is automatic. */
  use_masks: boolean;
  progress_dir: boolean;
}

/** `sfm/sfm_result.json` — the verdict that outlives the scrollback (§7.1). */
export interface SfmResult {
  exit_code: number;
  exit_meaning: string;
  spirula_version: string;
  images: number;
  sparse_models: number;
  masks_used: boolean;
  mask_count: number;
  quality: string;
  data_type: string;
  camera_model: string;
  command: string[];
  finished_at: string;
  registered?: number;
  total?: number;
  reprojection_mean_px?: number;
  reprojection_median_px?: number;
  observations?: number;
  points?: number;
  /** Camera *groups* — two intrinsics from one folder, not two components. */
  camera_groups?: number;
  elapsed_s?: number;
}

// ── Step 4 — `spirula train` (CLAUDE.md §7.6) ────────────────────────────────

export type TrainPreset =
  | '3dgs' | '360-camera' | 'in-the-wild' | 'linear-color' | 'synthetic'
  | 'meshing' | 'academic-baseline';

/**
 * Step 4's knobs, and `null` is the important value here.
 *
 * The preset is the first positional argument of `spirula train` and it moves
 * the defaults of everything under it — `meshing` alone sets `--primitive
 * 3dgut`, `--sh-degree 0` and `--background-mode noise`. Naming a flag
 * overrides the preset, so a knob holding a concrete number could not be told
 * from a knob the user never touched, and switching preset would send the old
 * one's whole block back and undo the new one. `null` means "the preset
 * decides" and nothing goes on the command line; `TRAIN_PRESET_DEFAULTS` (in
 * `TrainSettings.tsx`) is what the panel shows in its place, mirroring
 * `_PRESET_DEFAULTS` in `step_train.py`.
 *
 * The `load_*` switches are the exception: they are intent ("use the masks if
 * there are any"), resolved by the run against what is on disk.
 */
export interface TrainDefaults {
  preset: TrainPreset;
  num_iterations: number | null;
  quality: 'low' | 'medium' | 'high' | 'ultra' | null;
  cap_max: number | null;
  sh_degree: number | null;
  primitive: '3dgs' | 'mip' | '3dgut' | null;
  background_mode: 'black' | 'noise' | 'sh' | null;
  steps_per_save: number | null;
  save_only_latest_checkpoint: boolean | null;
  save_eval_images: boolean | null;
  distraction_robustness: 'off' | 'mild' | 'strong' | null;
  floater_suppression: 'off' | 'mild' | 'strong' | null;
  load_masks: boolean;
  /** Always 1 under the masked route — 0 means *ignore*, which measured as no
   *  masks at all (§7.6). No switch is offered for it. */
  apply_loss_for_mask: boolean;
  mask_boundary_offset: number | null;
  load_depths: boolean;
  load_normals: boolean;
  depth_supervision_weight: number | null;
  normal_supervision_weight: number | null;
  orientation_method: 'pca' | 'up' | 'vertical' | 'none' | 'gsplat' | null;
  center_method: 'poses' | 'focus' | 'none' | 'gsplat' | null;
  auto_scale_poses: boolean | null;
  train_frame: 'normalized' | 'camera' | 'points' | null;
}

/** `train/train_result.json` — the run's numbers, kept past the scrollback. */
export interface TrainResult {
  exit_code: number;
  spirula_version: string;
  preset: TrainPreset;
  images: number;
  iterations_requested: number;
  quality: string;
  cap_max: number;
  primitive: string;
  sh_degree: number;
  masks_used: boolean;
  mask_count: number;
  apply_loss_for_mask: boolean;
  depths_used: boolean;
  normals_used: boolean;
  /** Relative to the project directory. Null only on a run that wrote nothing. */
  splat_path: string | null;
  splat_bytes: number | null;
  /**
   * Gaussians in the written PLY, from its header — not the same number as
   * `num_gaussians`, which is the live count on the last bar line and is taken
   * before the final prune. Two 30 000-iteration runs printed the 1 000 000 cap
   * and wrote 715 890 and 716 831.
   */
  splat_count?: number | null;
  command: string[];
  finished_at: string;
  /** From the closing `Training complete. Steps: N   Time: M:SS` line. */
  steps?: number;
  elapsed_s?: number;
  /** From the last bar line the run printed. */
  iteration?: number;
  total_iterations?: number;
  num_gaussians?: number;
  loss?: number;
  ssim?: number;
  psnr?: number;
}

export interface AppDefaults {
  schema_version: number;
  extract: ExtractDefaults;
  curate: CurateDefaults;
  sfm: SfmDefaults;
  train: TrainDefaults;
  export: ExportDefaults;
  blender: BlenderDefaults;
  viewer: ViewerDefaults;
}

export type DefaultsSection = keyof Omit<AppDefaults, 'schema_version'>;

// ── Curation (wizard step 2, phase 2 — CLAUDE.md §6.3) ───────────────────────

export type Verdict = 'kept' | 'rejected';
export type RejectReason = 'blur' | 'redundant' | 'manual';
export type Override = 'keep' | 'drop';

export interface FrameInfo {
  filename: string;
  path: string;
  size_bytes: number;
  url: string;
  index: number;
  sequence_id: number | null;
  sharpness: number | null;
  sharpness_median: number | null;
  displacement_pct: number | null;
  /** null before the first analysis — an unanalysed set has no verdict. */
  verdict: Verdict | null;
  reason: RejectReason | null;
  warning: 'gap' | null;
  override: Override | null;
}

export interface SelectionSummary {
  total: number;
  removed: number;
  removed_pct: number;
  kept: number;
  rejected_blur: number;
  rejected_redundant: number;
  rejected_manual: number;
  kept_manual: number;
  warning_gap: number;
}

export interface FramesResponse {
  frames: FrameInfo[];
  total: number;
  kept_count: number;
  rejected_count: number;
  warning_count: number;
  analysed: boolean;
  summary: SelectionSummary | null;
}

export interface FrameScore {
  index: number;
  filename: string;
  sequence_id: number;
  sharpness: number;
  sharpness_median: number;
  displacement_pct: number | null;
  auto_verdict: Verdict;
  auto_reason: RejectReason | null;
  warning: 'gap' | null;
}

export interface SequenceSpan {
  id: number;
  start_index: number;
  end_index: number;
  frame_count: number;
}

export interface CurationScores {
  generated_at: string;
  params: CurateDefaults & {
    scene_method: string;
    band_source: string;
    working_fps: number | null;
  };
  sequences: SequenceSpan[];
  stats: {
    sharpness_all: { mean: number; median: number; min: number; max: number };
    sharpness_kept: { mean: number; median: number; min: number; max: number };
    overlap: { pairs: number; in_band: number; in_band_ratio: number; median_pct: number };
  };
  frames: FrameScore[];
}

export interface CurationSelection {
  generated_at: string;
  kept: string[];
  rejected: { frame: string; reason: RejectReason; index: number }[];
  warnings: { frame: string; reason: 'gap'; index: number }[];
  sequences: { id: number; frame_count: number; kept: number }[];
  summary: SelectionSummary;
}

export interface AnalysisResponse {
  scores: CurationScores | null;
  selection: CurationSelection | null;
  overrides: Record<string, Override>;
  extract: {
    working_fps: number | null;
    fps_explanation: string;
    input_video: string | null;
    mpdecimate: boolean;
    capture_preset: string;
    frame_count: number;
  } | null;
  analysed: boolean;
}

// -- Input sources (wizard steps 1 and 2) ------------------------------------

/** Raw ffprobe reading of one source file — the shape `core/probe.py` returns. */
export interface SourceProbe {
  path: string;
  container: string | null;
  codec: string | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  duration_s: number | null;
  bitrate: number | null;
  hdr: boolean;
  pix_fmt: string | null;
  nb_frames: string | null;
}

export interface SourceFile {
  filename: string;
  kind: 'video' | 'subtitle';
  size_bytes: number;
  /** Epoch seconds, as the filesystem reports them. */
  modified: number;
  /** Under /static — playable in a <video>, which is what the mini player uses. */
  url: string;
  /** Poster frame, or null when ffmpeg could not produce one. */
  thumb_url: string | null;
  probe: SourceProbe | null;
  probe_error: string | null;
  /** The single video step 2 will extract from (first .mp4, else first .mov). */
  is_extraction_source: boolean;
}

/** A folder of already-extracted frames, imported from disk or from a zip. */
export interface ImageSet {
  name: string;
  kind: 'images';
  image_count: number;
  total_bytes: number;
  avg_bytes: number;
  /** How many images of each extension: `{ '.png': 312 }`. */
  formats: Record<string, number>;
  width: number | null;
  height: number | null;
  /** False when the sampled images do not all share one resolution. */
  uniform_size: boolean;
  /** What the set would last at `nominal_fps` — a unit, not a property of the
   *  set: nothing in the curation of an image set reads a timecode. */
  duration_s: number;
  nominal_fps: number;
  /** The images declare an alpha channel (PNG colour type 4 or 6). */
  has_alpha: boolean;
  /** Sampled: some pixel is actually non-opaque. `null` = not verified. */
  alpha_in_use: boolean | null;
  /** The conformed name the import wrote: `my_shoot_%04d`. */
  pattern: string;
  /** How the originals were named before the import: `DSC_####.JPG`. */
  original_pattern: string;
  origin: 'zip' | 'folder' | 'upload';
  origin_name: string;
  origin_path: string;
  imported_at: string | null;
  first_image: string | null;
  url: string | null;
  thumb_url?: string | null;
  is_extraction_source?: boolean;
}

export type InputSourceKind = 'video' | 'images' | 'none';

export interface SourcesResponse {
  sources: SourceFile[];
  image_sets: ImageSet[];
  extraction_source: string | null;
  /** What step 2 will read. An imported set wins over a video in `input/`. */
  source_kind: InputSourceKind;
  source_name: string | null;
  video_count: number;
  ffmpeg_available: boolean;
}

/** What an import wrote, as `input/<set>/imageset.json` records it. */
export interface ImageSetManifest {
  name: string;
  origin: 'zip' | 'folder' | 'upload';
  origin_name: string;
  origin_path: string;
  imported_at: string;
  image_count: number;
  pattern: string;
  original_pattern: string;
  files: { index: number; filename: string; original: string }[];
}

// -- 3D viewer (wizard steps 3, 4 and 5) -------------------------------------

/**
 * What the viewer can ask for. `sfm` is step 3's sparse model (COLMAP binary,
 * not a PLY), `train` is the trained splat of the highest checkpoint. Step 5's
 * mesh is deliberately absent — a textured glb is neither a point cloud nor a
 * splat, and whether it gets a third renderer or a thumbnail is still open
 * (CLAUDE.md §13.6).
 */
export type PreviewSource = 'sfm' | 'train';

/** What the source file turned out to be, not which step wrote it: a step may
 *  produce a plain sparse cloud or a gaussian PLY, and the renderer follows the
 *  file. */
export type PreviewKind = 'cloud' | 'splat';

export interface PreviewState {
  source: PreviewSource;
  available: boolean;
  ready: boolean;
  /** Vertex cap the preview was built at; null means the whole file. */
  max_count: number | null;
  source_file?: string;
  source_bytes?: number;
  source_url?: string;
  kind?: PreviewKind;
  /** Vertices in the source file. */
  total?: number;
  /** URL of the built preview, under /static. */
  url?: string;
  /** Vertices actually in the preview. */
  count?: number;
  bytes?: number;
  decimated?: boolean;
  building?: boolean;
  progress?: number;
  error?: string;
}

export interface CameraPose {
  /** The input filename — `images.bin` keeps it, and nothing renames frames. */
  name: string;
  position: number[];
  /**
   * Row-major 3x3 world-from-camera rotation. COLMAP-framed, so the camera
   * looks down +Z and `basis · (0, 0, 1)` is the viewing direction.
   */
  basis: number[];
  /** The input frame this camera came from. Always `name` now. */
  source_name: string | null;
  sequence_id: number | null;
  /** An aligned camera whose neighbour in the input order never came back. */
  gap_edge: boolean;
}

export interface CamerasReport {
  available: boolean;
  count: number;
  cameras: CameraPose[];
  matched_by?: 'name' | 'position' | 'count' | null;
  gaps_known?: boolean;
  missing_count?: number;
  input_count?: number;
  sequence_ids?: number[];
  /**
   * Horizontal field of view in radians, off `cameras.bin`. Null for the lens
   * models no single angle describes — a fisheye or an equirectangular camera
   * (CLAUDE.md §7.1) — where the overlay draws its own frustum shape instead.
   */
  fov_x?: number | null;
  aspect?: number | null;
  /** `sparse/N` count. More than one is a fragmented capture. */
  models?: number;
}
