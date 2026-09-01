import React, { useEffect, useMemo, useState } from 'react';
import {
  Box,
  Clapperboard,
  Filter,
  FolderCog,
  Layers,
  Mountain,
  Orbit,
  PackageOpen,
  BrainCircuit,
  Gauge,
  RotateCcw,
  Scan,
  Sparkles,
  TriangleAlert,
} from 'lucide-react';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Separator } from '@/components/ui/separator';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import TrainSettings from '@/components/settings/TrainSettings';
import MaskSettings from '@/components/settings/MaskSettings';
import GeometrySettings from '@/components/settings/GeometrySettings';
import MeshSettings from '@/components/settings/MeshSettings';
import CheckpointsSection from '@/components/settings/CheckpointsSection';
import HardwareSection from '@/components/settings/HardwareSection';
import { useDefaults } from '@/hooks/useDefaults';
import { useSettings } from '@/hooks/useSettings';
import type { AppDefaults, DefaultsSection } from '@/types';

/* ── Small field primitives ─────────────────────────────────────────────── */

const Row: React.FC<{
  label: string;
  hint?: string;
  wide?: boolean;
  children: React.ReactNode;
}> = ({ label, hint, wide, children }) => (
  <div className="flex items-start justify-between gap-6 py-2">
    <div className="min-w-0">
      <Label className="text-slate-200">{label}</Label>
      {hint && <p className="text-xs text-slate-500 mt-0.5 leading-snug">{hint}</p>}
    </div>
    <div className={wide ? 'shrink-0 w-[380px]' : 'shrink-0 w-[220px]'}>{children}</div>
  </div>
);

const NumField: React.FC<{
  value: number;
  onChange: (v: number) => void;
  step?: number;
  min?: number;
  max?: number;
  disabled?: boolean;
}> = ({ value, onChange, step = 1, min, max, disabled }) => (
  <Input
    type="number"
    step={step}
    min={min}
    max={max}
    value={value}
    disabled={disabled}
    onChange={(e) => {
      const n = Number(e.target.value);
      if (!Number.isNaN(n)) onChange(n);
    }}
    className="bg-slate-950 border-slate-700 text-slate-100 disabled:opacity-50"
  />
);

const TextField: React.FC<{ value: string; onChange: (v: string) => void; placeholder?: string }> = ({
  value,
  onChange,
  placeholder,
}) => (
  <Input
    value={value}
    placeholder={placeholder}
    onChange={(e) => onChange(e.target.value)}
    className="bg-slate-950 border-slate-700 text-slate-100"
  />
);

/**
 * Editable free-text path box.
 *
 * Tool paths go straight to config.json, and `updateSettings` replaces the
 * whole settings object with the server response. Committing per keystroke
 * therefore raced the PUT and reset the caret, which made the box look
 * read-only. Keystrokes stay local; the commit happens on blur or Enter,
 * Escape reverts.
 */
const PathField: React.FC<{
  value: string;
  onCommit: (v: string) => void;
  placeholder?: string;
}> = ({ value, onCommit, placeholder }) => {
  const [local, setLocal] = useState(value);
  const [editing, setEditing] = useState(false);

  // Adopt external changes only while the user is not typing in this box.
  useEffect(() => {
    if (!editing) setLocal(value);
  }, [value, editing]);

  const commit = () => {
    setEditing(false);
    const trimmed = local.trim();
    if (trimmed !== local) setLocal(trimmed);
    if (trimmed !== value) onCommit(trimmed);
  };

  return (
    <Input
      value={local}
      placeholder={placeholder}
      spellCheck={false}
      autoComplete="off"
      title={local || placeholder}
      onFocus={() => setEditing(true)}
      onChange={(e) => setLocal(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          e.currentTarget.blur();
        } else if (e.key === 'Escape') {
          e.preventDefault();
          setLocal(value);
          setEditing(false);
          e.currentTarget.blur();
        }
      }}
      className="bg-slate-950 border-slate-700 text-slate-100 font-mono text-xs"
    />
  );
};

/** Groups a run of rows under a heading, for sections long enough to need one. */
const SubHeading: React.FC<{ title: string; note?: string }> = ({ title, note }) => (
  <div className="pt-5 pb-1">
    <p className="text-xs font-semibold uppercase tracking-wide text-cyan-400">{title}</p>
    {note && <p className="text-xs text-slate-500 mt-1 leading-snug max-w-prose">{note}</p>}
  </div>
);

const Choice: React.FC<{
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}> = ({ value, onChange, options }) => (
  <Select value={value} onValueChange={onChange}>
    <SelectTrigger className="bg-slate-950 border-slate-700 text-slate-100">
      <SelectValue />
    </SelectTrigger>
    <SelectContent className="bg-slate-800 border-slate-700 text-slate-100">
      {options.map((o) => (
        <SelectItem key={o.value} value={o.value}>
          {o.label}
        </SelectItem>
      ))}
    </SelectContent>
  </Select>
);

/* ── Sections ───────────────────────────────────────────────────────────── */

// Three sections of this panel are not `defaults.json` sections at all:
// `tools` is config.json (layer 1 of CLAUDE.md §4), `models` is the checkpoint
// cache on this machine, and `hardware` is the machine itself. All three are
// installation state, which is why they live in the global panel and not on a
// wizard step. `hardware` goes one further and is not state at all — it is
// read-only, so it has no reset and nothing to save.
type SectionId = DefaultsSection | 'tools' | 'models' | 'hardware';

const SECTIONS: { id: SectionId; label: string; icon: React.ElementType }[] = [
  { id: 'extract', label: 'Extraction', icon: Clapperboard },
  { id: 'curate', label: 'Curation', icon: Filter },
  { id: 'sfm', label: 'SfM', icon: Scan },
  { id: 'sam', label: 'Masks', icon: Layers },
  { id: 'geometry', label: 'Geometry', icon: Mountain },
  { id: 'train', label: 'Training', icon: Sparkles },
  { id: 'mesh', label: 'Mesh', icon: Box },
  { id: 'export', label: 'Export', icon: PackageOpen },
  { id: 'viewer', label: '3D viewer', icon: Orbit },
  { id: 'tools', label: 'Tools', icon: FolderCog },
  { id: 'models', label: 'Checkpoints', icon: BrainCircuit },
  { id: 'hardware', label: 'Hardware', icon: Gauge },
];

// Sections with nothing to edit: no draft, no Save, no factory reset.
const READ_ONLY_SECTIONS: SectionId[] = ['models', 'hardware'];

interface AppSetupPanelProps {
  open: boolean;
  onClose: () => void;
}

const AppSetupPanel: React.FC<AppSetupPanelProps> = ({ open, onClose }) => {
  const { defaults, presets, saving, error, updateDefaults, resetDefaults, previewFps } =
    useDefaults();
  const { settings, updateSettings } = useSettings();

  const [section, setSection] = useState<SectionId>('extract');
  // Local draft so typing does not fire a PUT per keystroke.
  const [draft, setDraft] = useState<AppDefaults | null>(null);
  const [sampleFps, setSampleFps] = useState(100);
  const [sampleDuration, setSampleDuration] = useState(120);
  const [fpsPreview, setFpsPreview] = useState<string>('');

  useEffect(() => {
    if (defaults) setDraft(defaults);
  }, [defaults]);

  const dirty = useMemo(
    () => !!draft && !!defaults && JSON.stringify(draft) !== JSON.stringify(defaults),
    [draft, defaults],
  );

  // Resolve the fps policy against a sample source, so the number is never a
  // black box. Debounced — every keystroke would otherwise hit the backend.
  useEffect(() => {
    if (!draft || section !== 'extract') return;
    const t = setTimeout(() => {
      previewFps(draft.extract, sampleFps || null, sampleDuration || null)
        .then((r) => setFpsPreview(r.explanation))
        .catch(() => setFpsPreview(''));
    }, 250);
    return () => clearTimeout(t);
  }, [draft, section, sampleFps, sampleDuration, previewFps]);

  const patch = <S extends DefaultsSection>(s: S, key: keyof AppDefaults[S], value: unknown) => {
    setDraft((d) => (d ? { ...d, [s]: { ...d[s], [key]: value } } : d));
  };

  const handleSave = async () => {
    if (!draft) return;
    await updateDefaults(draft);
  };

  const handleResetSection = async () => {
    if (section === 'tools' || section === 'models' || section === 'hardware') return;
    await resetDefaults(section);
  };

  const activePreset = presets.find((p) => p.id === draft?.extract.capture_preset);

  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()}>
      <SheetContent
        side="right"
        className="sm:max-w-none w-[840px] max-w-[95vw] flex flex-col p-0"
      >
        <SheetHeader className="px-6 py-4 border-b border-slate-700">
          <SheetTitle className="text-slate-100">Application setup</SheetTitle>
          <p className="text-xs text-slate-500">
            Defaults applied to every new project. A project that overrides a value keeps its own.
          </p>
        </SheetHeader>

        <div className="flex flex-1 overflow-hidden">
          {/* Section rail */}
          <nav className="w-[190px] shrink-0 border-r border-slate-700 overflow-y-auto py-2">
            {SECTIONS.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                onClick={() => setSection(id)}
                className={`w-full flex items-center gap-2 px-4 py-2 text-sm text-left transition-colors ${
                  section === id
                    ? 'bg-slate-800 text-cyan-400 border-l-2 border-cyan-400'
                    : 'text-slate-400 hover:text-slate-100 hover:bg-slate-800/50 border-l-2 border-transparent'
                }`}
              >
                <Icon className="w-4 h-4 shrink-0" />
                {label}
              </button>
            ))}
          </nav>

          {/* Section body */}
          <div className="flex-1 overflow-y-auto px-6 py-4">
            {!draft && <p className="text-slate-500 text-sm">Loading defaults…</p>}

            {draft && section === 'extract' && (
              <div className="divide-y divide-slate-800">
                <Row
                  label="Capture preset"
                  hint={activePreset?.notes ?? 'Sets the target frame count and the overlap band.'}
                >
                  <Choice
                    value={draft.extract.capture_preset}
                    onChange={(v) => patch('extract', 'capture_preset', v)}
                    options={presets.map((p) => ({ value: p.id, label: p.label }))}
                  />
                </Row>

                <Row
                  label="Working fps mode"
                  hint="auto = target frames ÷ probed duration. ratio = fraction of the source cadence. absolute = a fixed number."
                >
                  <Choice
                    value={draft.extract.fps_mode}
                    onChange={(v) => patch('extract', 'fps_mode', v)}
                    options={[
                      { value: 'auto', label: 'Auto (from source)' },
                      { value: 'ratio', label: 'Ratio of source fps' },
                      { value: 'absolute', label: 'Absolute fps' },
                    ]}
                  />
                </Row>

                {draft.extract.fps_mode === 'auto' && (
                  <Row
                    label="Target frame count"
                    hint={
                      activePreset
                        ? `Clamped to ${activePreset.min_fps}–${activePreset.max_fps} fps by the preset.`
                        : undefined
                    }
                  >
                    <NumField
                      value={draft.extract.target_frame_count}
                      min={10}
                      step={10}
                      onChange={(v) => patch('extract', 'target_frame_count', v)}
                    />
                  </Row>
                )}

                <Row
                  label="Fps ratio"
                  hint="Used by ratio mode, and as the fallback when ffprobe gives no duration. 0.2 matches the RealityScan video-import default."
                >
                  <NumField
                    value={draft.extract.fps_ratio}
                    step={0.05}
                    min={0.01}
                    onChange={(v) => patch('extract', 'fps_ratio', v)}
                  />
                </Row>

                <Row label="Absolute fps" hint="Used by absolute mode, and when the source cadence is unknown.">
                  <NumField
                    value={draft.extract.fps_absolute}
                    step={0.5}
                    min={0.1}
                    onChange={(v) => patch('extract', 'fps_absolute', v)}
                  />
                </Row>

                <Row
                  label="Remove duplicate frames (mpdecimate)"
                  hint="Off by default: it duplicates the overlap gate and drops frames non-deterministically, breaking the frame-index ↔ timecode mapping curation relies on."
                >
                  <div className="flex items-center gap-2">
                    <Switch
                      checked={draft.extract.mpdecimate}
                      onCheckedChange={(v) => patch('extract', 'mpdecimate', v)}
                    />
                    {draft.extract.mpdecimate && (
                      <span className="flex items-center gap-1 text-xs text-amber-400">
                        <TriangleAlert className="w-3.5 h-3.5" />
                        breaks curation
                      </span>
                    )}
                  </div>
                </Row>

                <Row
                  label="JPEG compression quality"
                  hint="FFmpeg -qscale:v — 1 is best, 5 is worst. Compression only: it changes the file weight and the artefacts, never the pixel dimensions."
                >
                  <NumField
                    value={draft.extract.quality}
                    min={1}
                    max={31}
                    onChange={(v) => patch('extract', 'quality', v)}
                  />
                </Row>

                <Row
                  label="Output resolution (% of source)"
                  hint="Downscales every extracted frame (FFmpeg scale, after the fps gate). 100 writes the source resolution; both sides are truncated to an even number for the mjpeg encoder."
                >
                  <div className="flex items-center gap-2">
                    <NumField
                      value={draft.extract.scale_percent}
                      min={10}
                      max={100}
                      step={5}
                      onChange={(v) => patch('extract', 'scale_percent', v)}
                    />
                    {draft.extract.scale_percent <= 50 && (
                      <span className="flex items-center gap-1 text-xs text-amber-400">
                        <TriangleAlert className="w-3.5 h-3.5" />
                        alignment detail
                      </span>
                    )}
                  </div>
                </Row>

                <Row label="Max frames" hint="0 = unlimited.">
                  <NumField
                    value={draft.extract.max_frames}
                    min={0}
                    step={10}
                    onChange={(v) => patch('extract', 'max_frames', v)}
                  />
                </Row>

                {/* Policy preview */}
                <div className="pt-4">
                  <p className="text-xs uppercase tracking-wide text-slate-500 mb-2">
                    Preview on a sample source
                  </p>
                  <div className="flex items-end gap-3">
                    <div className="w-[120px]">
                      <Label className="text-xs text-slate-400">Source fps</Label>
                      <NumField value={sampleFps} step={5} min={1} onChange={setSampleFps} />
                    </div>
                    <div className="w-[120px]">
                      <Label className="text-xs text-slate-400">Duration (s)</Label>
                      <NumField value={sampleDuration} step={10} min={1} onChange={setSampleDuration} />
                    </div>
                  </div>
                  {fpsPreview && (
                    <p className="mt-3 text-sm text-cyan-400 font-mono bg-slate-950 border border-slate-800 rounded px-3 py-2">
                      {fpsPreview}
                    </p>
                  )}
                </div>
              </div>
            )}

            {draft && section === 'curate' && (
              <div className="divide-y divide-slate-800">
                <Row label="Curation enabled" hint="Blur rejection, scene cuts and the overlap gate.">
                  <Switch
                    checked={draft.curate.enabled}
                    onCheckedChange={(v) => patch('curate', 'enabled', v)}
                  />
                </Row>
                <Row
                  label="Run automatically after extraction"
                  hint="Analysis can always be re-run alone from step 2, without re-extracting."
                >
                  <Switch
                    checked={draft.curate.auto_after_extract}
                    onCheckedChange={(v) => patch('curate', 'auto_after_extract', v)}
                  />
                </Row>
                <Row label="Scene detector" hint="Adaptive tolerates constant camera motion such as orbits.">
                  <Choice
                    value={draft.curate.scene_detector}
                    onChange={(v) => patch('curate', 'scene_detector', v)}
                    options={[
                      { value: 'adaptive', label: 'Adaptive' },
                      { value: 'content', label: 'Content' },
                      { value: 'off', label: 'Off' },
                    ]}
                  />
                </Row>
                <Row
                  label="Cut detection source"
                  hint="Auto uses the scene scores FFmpeg captures during the extraction, so curation never decodes the video a second time (measured: 5 s of extra extraction against 318 s of PySceneDetect on a 52 s rush). It falls back on its own when those scores are missing. Pin to PySceneDetect to use the reference detector."
                >
                  <Choice
                    value={draft.curate.cut_source}
                    onChange={(v) => patch('curate', 'cut_source', v)}
                    options={[
                      { value: 'auto', label: 'Auto (from extraction)' },
                      { value: 'video', label: 'PySceneDetect' },
                      { value: 'frames', label: 'Extracted frames' },
                    ]}
                  />
                </Row>
                <Row label="Min scene length" hint="Frames. Shorter cuts are merged into the neighbouring sequence.">
                  <NumField
                    value={draft.curate.min_scene_len}
                    min={1}
                    onChange={(v) => patch('curate', 'min_scene_len', v)}
                  />
                </Row>
                <Row
                  label="Sharpness window"
                  hint="Rolling median window, in frames. Rejection is relative to this window — never an absolute threshold."
                >
                  <NumField
                    value={draft.curate.sharpness_window}
                    min={3}
                    onChange={(v) => patch('curate', 'sharpness_window', v)}
                  />
                </Row>
                <Row label="Sharpness sensitivity" hint="0–100. Higher rejects more aggressively.">
                  <NumField
                    value={draft.curate.sharpness_sensitivity}
                    min={0}
                    onChange={(v) => patch('curate', 'sharpness_sensitivity', v)}
                  />
                </Row>
                <Row
                  label="Overlap band from capture preset"
                  hint={
                    activePreset
                      ? `The preset carries the band along with the target frame count — '${activePreset.label}' means ${activePreset.overlap_min_step_pct}–${activePreset.overlap_band_max_pct}%. Turn this off to pin the two values below by hand.`
                      : 'The preset carries the band along with the target frame count. Turn this off to pin the two values below by hand.'
                  }
                >
                  <Switch
                    checked={draft.curate.overlap_from_preset}
                    onCheckedChange={(v) => patch('curate', 'overlap_from_preset', v)}
                  />
                </Row>
                <Row
                  label="Overlap min step (%)"
                  hint="Below this ORB displacement the frame is redundant and gets dropped."
                >
                  <NumField
                    value={
                      draft.curate.overlap_from_preset && activePreset
                        ? activePreset.overlap_min_step_pct
                        : draft.curate.overlap_min_step_pct
                    }
                    step={0.5}
                    min={0}
                    disabled={draft.curate.overlap_from_preset}
                    onChange={(v) => patch('curate', 'overlap_min_step_pct', v)}
                  />
                </Row>
                <Row
                  label="Overlap max step (%)"
                  hint="Above this the frame is kept but flagged as an alignment-risk gap."
                >
                  <NumField
                    value={
                      draft.curate.overlap_from_preset && activePreset
                        ? activePreset.overlap_band_max_pct
                        : draft.curate.overlap_band_max_pct
                    }
                    step={0.5}
                    min={0}
                    disabled={draft.curate.overlap_from_preset}
                    onChange={(v) => patch('curate', 'overlap_band_max_pct', v)}
                  />
                </Row>
              </div>
            )}

            {draft && section === 'sfm' && (
              <div className="divide-y divide-slate-800">
                <SubHeading
                  title="Reconstruction"
                  note="`spirula sfm auto` exposes two headline knobs and reports what they moved: --quality sets the working resolution, the feature budget and the pair-selection breadth, --data-type says what the capture is. Everything below them is left at the build's own value unless you move it here — naming a flag explicitly overrides what the preset set it to, so a knob at its default is deliberately not sent."
                />
                <Row
                  label="Quality"
                  hint="The build's default is high. medium reconstructed 251/251 images at 0.50 px mean reprojection in 34.6 s on this workstation."
                >
                  <Choice
                    value={draft.sfm.quality}
                    onChange={(v) => patch('sfm', 'quality', v)}
                    options={[
                      { value: 'low', label: 'Low' },
                      { value: 'medium', label: 'Medium' },
                      { value: 'high', label: 'High (build default)' },
                      { value: 'extreme', label: 'Extreme' },
                    ]}
                  />
                </Row>
                <Row
                  label="Data type"
                  hint="video switches pair selection to sequential plus loop closure, which is what a project that came through step 2's frame extraction is."
                >
                  <Choice
                    value={draft.sfm.data_type}
                    onChange={(v) => patch('sfm', 'data_type', v)}
                    options={[
                      { value: 'individual', label: 'Individual photos' },
                      { value: 'video', label: 'Video frames' },
                      { value: 'internet', label: 'Internet collection' },
                    ]}
                  />
                </Row>
                <Row
                  label="Camera model"
                  hint="360 and fisheye capture is a first-class input: spirula reads equirectangular and >180° fisheye natively, with no undistortion pass anywhere in this pipeline."
                >
                  <Choice
                    value={draft.sfm.camera_model}
                    onChange={(v) => patch('sfm', 'camera_model', v)}
                    options={[
                      { value: 'simple-pinhole', label: 'Simple pinhole' },
                      { value: 'pinhole', label: 'Pinhole' },
                      { value: 'radial', label: 'Radial' },
                      { value: 'opencv', label: 'OpenCV (build default)' },
                      { value: 'full-opencv', label: 'Full OpenCV' },
                      { value: 'opencv-fisheye', label: 'OpenCV fisheye' },
                      { value: 'thin-prism-fisheye', label: 'Thin-prism fisheye' },
                      { value: 'equirectangular', label: 'Equirectangular (360°)' },
                    ]}
                  />
                </Row>
                <SubHeading
                  title="Overrides"
                  note="Each of these is sent only while it differs from the build's own default — otherwise --quality and --data-type keep deciding it, and the run's own 'The presets set --max-features to 4096 (was 8192)' lines say what they chose."
                />
                <Row
                  label="Pairs"
                  hint="auto is GPU pair selection at 100 images or more, sequential (plus loop closure) for video below that, and exhaustive otherwise."
                >
                  <Choice
                    value={draft.sfm.pairs}
                    onChange={(v) => patch('sfm', 'pairs', v)}
                    options={[
                      { value: 'auto', label: 'Auto (build default)' },
                      { value: 'exhaustive', label: 'Exhaustive' },
                      { value: 'sequential', label: 'Sequential' },
                      { value: 'prefilter', label: 'Prefilter' },
                    ]}
                  />
                </Row>
                <Row
                  label="Camera grouping"
                  hint="How images are grouped into cameras. Every mode splits on image resolution first, which is why one folder can report two cameras — those are two intrinsics, not two reconstructions."
                >
                  <Choice
                    value={draft.sfm.camera_mode}
                    onChange={(v) => patch('sfm', 'camera_mode', v)}
                    options={[
                      { value: 'single', label: 'Single' },
                      { value: 'folder', label: 'Folder (build default)' },
                      { value: 'image', label: 'Per image' },
                    ]}
                  />
                </Row>
                <Row
                  label="Max image size"
                  hint="Longest edge the extractor runs on; keypoints are still reported in the source image's pixels. 0 lets the frontend pick — 3200 for sift, 1600 for aliked — and is what --quality moves."
                >
                  <NumField
                    value={draft.sfm.max_image_size}
                    step={200}
                    min={0}
                    onChange={(v) => patch('sfm', 'max_image_size', v)}
                  />
                </Row>
                <Row
                  label="Max features"
                  hint="Keypoints kept per image, largest scales first. 8192 is the build default; --quality medium lowers it to 4096 on its own."
                >
                  <NumField
                    value={draft.sfm.max_features}
                    step={1024}
                    min={256}
                    onChange={(v) => patch('sfm', 'max_features', v)}
                  />
                </Row>
                <SubHeading
                  title="Masks and progress"
                  note="masks/ is already a sibling of frames/, which is the layout sfm auto adopts by itself — so using the masks costs no flag and only refusing them does."
                />
                <Row
                  label="Use masks"
                  hint="Off sends --no-masks and reconstructs on the full frames even when masks/ holds one image per frame. Keypoints on black pixels are dropped when it is on."
                >
                  <Switch
                    checked={draft.sfm.use_masks}
                    onCheckedChange={(v) => patch('sfm', 'use_masks', v)}
                  />
                </Row>
                <Row
                  label="Write progress snapshots"
                  hint="--progress-dir writes model.bin and pairs.bin while the run goes, for a front end that shows the reconstruction assembling rather than tailing its log. Nothing reads them yet (TODO P5), and they cost disk."
                >
                  <Switch
                    checked={draft.sfm.progress_dir}
                    onCheckedChange={(v) => patch('sfm', 'progress_dir', v)}
                  />
                </Row>
              </div>
            )}

            {/* Layer 2 for step 4 is the same panel step 4 shows, and
                deliberately the same component: it carries the per-preset
                default table, and a second copy of that table here would be a
                second thing to update the day the binary is. `null` is a real
                value in it - "the preset decides" - so the section resets to a
                block of nulls rather than to a frozen copy of `3dgs`'s
                numbers. */}
            {draft && section === 'train' && (
              <div className="pt-2">
                <TrainSettings
                  settings={draft.train}
                  onChange={(t) => setDraft((d) => (d ? { ...d, train: t } : d))}
                />
              </div>
            )}

            {/* Masking and geometry are re-runnable passes rather than steps,
                but their defaults are layer 2 like everything else. The same
                components the step panels use, so `maskRefusal` and the licence
                gate exist once. */}
            {draft && section === 'sam' && (
              <div className="pt-2">
                <MaskSettings
                  settings={draft.sam}
                  onChange={(sam) => setDraft((d) => (d ? { ...d, sam } : d))}
                />
              </div>
            )}

            {draft && section === 'geometry' && (
              <div className="pt-2">
                <GeometrySettings
                  settings={draft.geometry}
                  onChange={(g) => setDraft((d) => (d ? { ...d, geometry: g } : d))}
                />
              </div>
            )}

            {/* Layer 2 for step 5 is the same component step 5 shows, for the
                same reason the training section is: `meshRefusal` lives in it,
                and a second copy of the PLY/OBJ rule here would be a second
                thing to get wrong. */}
            {draft && section === 'mesh' && (
              <div className="pt-2">
                <MeshSettings
                  settings={draft.mesh}
                  onChange={(m) => setDraft((d) => (d ? { ...d, mesh: m } : d))}
                />
              </div>
            )}

            {/* The deliverable export of step 4's splat (CLAUDE.md §7.6c).
                Every field here bar the format is a *reduction*, and every one
                of them ships off: the default export is the trained splat byte
                for byte. Step 4's own panel shows the same section with the
                measured cost of each choice beside it. */}
            {draft && section === 'export' && (
              <div className="divide-y divide-slate-800">
                <Row
                  label="Format"
                  hint="SOG, SPZ and Compressed PLY are written by @playcanvas/splat-transform, which is optional; PLY and .splat need nothing."
                >
                  <Choice
                    value={draft.export.format}
                    onChange={(v) => patch('export', 'format', v)}
                    options={[
                      { value: 'ply', label: 'PLY' },
                      { value: 'splat', label: 'SPLAT' },
                      { value: 'sog', label: 'SOG' },
                      { value: 'spz', label: 'SPZ' },
                      { value: 'compressed-ply', label: 'Compressed PLY' },
                    ]}
                  />
                </Row>
                <Row
                  label="Spherical harmonics"
                  hint="Highest band kept. 'Keep' writes what the trainer wrote; 0 drops all 45 f_rest_* and 72.6 % of every vertex with them (measured 3.65x smaller)."
                >
                  <Choice
                    value={draft.export.sh_degree === null
                      ? 'keep' : String(draft.export.sh_degree)}
                    onChange={(v) => patch(
                      'export', 'sh_degree', v === 'keep' ? null : Number(v),
                    )}
                    options={[
                      { value: 'keep', label: 'Keep' },
                      { value: '2', label: '2' },
                      { value: '1', label: '1' },
                      { value: '0', label: '0' },
                    ]}
                  />
                </Row>
                <Row
                  label="Opacity floor"
                  hint="Linear alpha, 0 = off. Spirula trains at low opacity by design — the usual 1/255 threshold drops ~1 % of a splat here, and 0.05 dropped 43 %."
                >
                  <NumField
                    value={draft.export.opacity_min}
                    step={0.005}
                    min={0}
                    max={0.99}
                    onChange={(v) => patch('export', 'opacity_min', v)}
                  />
                </Row>
                <Row
                  label="Max gaussians"
                  hint="0 = no limit. Nothing is re-fitted after a cut, so a deep target thins the picture rather than simplifying it."
                >
                  <NumField
                    value={draft.export.max_count}
                    step={10000}
                    min={0}
                    onChange={(v) => patch('export', 'max_count', v)}
                  />
                </Row>
                <Row
                  label="Which gaussians"
                  hint="How the cap chooses: by opacity x volume, or an even spread over the file (never the first N — a PLY is not shuffled)."
                >
                  <Choice
                    value={draft.export.selection}
                    onChange={(v) => patch('export', 'selection', v)}
                    options={[
                      { value: 'importance', label: 'Importance' },
                      { value: 'uniform', label: 'Evenly' },
                    ]}
                  />
                </Row>
                <Row
                  label="Naming pattern"
                  hint="Tokens: {project}, {seq}, {index:05d}, {fps}"
                >
                  <TextField
                    value={draft.export.pattern}
                    onChange={(v) => patch('export', 'pattern', v)}
                  />
                </Row>
              </div>
            )}

            {draft && section === 'viewer' && (
              <div className="divide-y divide-slate-800">
                <p className="text-xs text-slate-500 pb-3">
                  The 3D preview in steps 3, 4 and 5. It never loads the step output
                  directly — a trained splat runs to gigabytes — but a decimated copy
                  built next to it.
                </p>
                <Row
                  label="Open at"
                  hint="Points or gaussians loaded when the viewer opens. 0 opens at full
                        quality. The Full button always loads the whole file, whatever
                        this says."
                >
                  <NumField
                    value={draft.viewer.preview_max_points}
                    step={100000}
                    min={0}
                    onChange={(v) => patch('viewer', 'preview_max_points', Math.max(0, v))}
                  />
                </Row>
                <Row label="Point size" hint="Pixels, sparse clouds only.">
                  <NumField
                    value={draft.viewer.point_size}
                    step={0.1}
                    min={0.5}
                    onChange={(v) => patch('viewer', 'point_size', v)}
                  />
                </Row>
                <Row
                  label="Show cameras"
                  hint="The registered camera frustums, coloured per sequence, amber where
                        a neighbouring frame failed to align."
                >
                  <div className="flex justify-end">
                    <Switch
                      checked={draft.viewer.show_cameras}
                      onCheckedChange={(v) => patch('viewer', 'show_cameras', v)}
                    />
                  </div>
                </Row>
                <Row label="Show camera path" hint="The line joining the cameras, cut by cut.">
                  <div className="flex justify-end">
                    <Switch
                      checked={draft.viewer.show_camera_path}
                      onCheckedChange={(v) => patch('viewer', 'show_camera_path', v)}
                    />
                  </div>
                </Row>
                <Row label="Background">
                  <TextField
                    value={draft.viewer.background}
                    onChange={(v) => patch('viewer', 'background', v)}
                  />
                </Row>
              </div>
            )}

            {section === 'models' && <CheckpointsSection active={section === 'models'} />}

            {section === 'hardware' && <HardwareSection active={section === 'hardware'} />}

            {section === 'tools' && settings && (
              <div className="divide-y divide-slate-800">
                <p className="text-xs text-slate-500 pb-3">
                  Installation settings — stored in config.json. Type or paste a full path,
                  then press Enter or click away to save. Escape cancels.
                </p>
                {/* One binary drives steps 3 to 5 — sfm, train, mesh, sam and
                    geometry are tools inside it (CLAUDE.md §5.1). The two rows
                    this replaced named RealityScan and LichtFeld Studio, the
                    CUDA pair this project exists to be free of (§12,
                    2026-08-27); they had been writing config.json keys nothing
                    read since the port. */}
                <Row
                  label="Spirula Studio executable"
                  wide
                  hint="One binary, six tools — SfM, training, meshing, masking and geometry all live in it. `setup.py` fetches it into tools/spirula/."
                >
                  <PathField
                    value={settings.tools.spirula_exe_path ?? ''}
                    placeholder="tools/spirula/spirula.exe"
                    onCommit={(v) =>
                      updateSettings({
                        tools: { ...settings.tools, spirula_exe_path: v || null },
                      })
                    }
                  />
                </Row>
                <Row
                  label="Checkpoint cache"
                  wide
                  hint="Where the SAM and geometry checkpoints are installed. Empty means spirula's own model directory, which is where its automatic fetch lands — the one place a file this app installs and a file the tool downloads are the same file. Manage the checkpoints themselves under Checkpoints."
                >
                  <PathField
                    value={settings.tools.spirula_model_cache ?? ''}
                    placeholder="%LOCALAPPDATA%\spirula-studio\models"
                    onCommit={(v) =>
                      updateSettings({
                        tools: { ...settings.tools, spirula_model_cache: v || null },
                      })
                    }
                  />
                </Row>
                <Row label="FFmpeg executable" wide hint="ffprobe is looked up next to it.">
                  <PathField
                    value={settings.tools.ffmpeg_path ?? ''}
                    placeholder="C:\ffmpeg\bin\ffmpeg.exe"
                    onCommit={(v) =>
                      updateSettings({ tools: { ...settings.tools, ffmpeg_path: v } })
                    }
                  />
                </Row>
                <Row
                  label="Hardware video decoding"
                  hint="FFmpeg -hwaccel for the step 2 extraction. Measured on this workstation: 20 s of 4K/100fps HEVC decodes in 92.9 s on the CPU and 20.5 s on CUDA. FFmpeg falls back to software on its own when the GPU refuses a source, and step 2 says so in the log."
                >
                  <Choice
                    value={settings.tools.ffmpeg_hwaccel ?? 'none'}
                    onChange={(v) =>
                      updateSettings({ tools: { ...settings.tools, ffmpeg_hwaccel: v } })
                    }
                    options={[
                      { value: 'none', label: 'Off (CPU)' },
                      { value: 'auto', label: 'Auto' },
                      { value: 'cuda', label: 'CUDA / NVDEC' },
                      { value: 'd3d11va', label: 'D3D11VA' },
                      { value: 'dxva2', label: 'DXVA2' },
                      { value: 'qsv', label: 'Intel QSV' },
                      { value: 'vulkan', label: 'Vulkan' },
                    ]}
                  />
                </Row>
                {/* Optional, and the only thing in this app that wants it are
                    the three compressed export formats of CLAUDE.md §7.6c.
                    Empty is the normal state: the resolver looks in
                    `tools/splat-transform/` and then on PATH by itself. The row
                    it replaced was `supersplat_url`, which left config.json
                    with the two CUDA tools (§12, 2026-08-28) and had been a
                    field writing a key nothing read ever since. */}
                <Row
                  label="splat-transform"
                  hint="Optional — SOG / SPZ / compressed PLY exports only. Leave empty to use tools/splat-transform/, installed with: npm install --prefix tools/splat-transform @playcanvas/splat-transform"
                  wide
                >
                  <PathField
                    value={settings.tools.splat_transform_path ?? ''}
                    placeholder="tools/splat-transform/node_modules/.bin/splat-transform.cmd"
                    onCommit={(v) =>
                      updateSettings({
                        tools: { ...settings.tools, splat_transform_path: v || null },
                      })
                    }
                  />
                </Row>
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <Separator className="bg-slate-700" />
        <div className="flex items-center justify-between px-6 py-3">
          <div className="text-xs">
            {/* A read-only section has nothing to save, so the save state
                belongs to somewhere else and saying "Saved" under it would be
                answering a question nobody asked. */}
            {READ_ONLY_SECTIONS.includes(section) ? null : (
              <>
                {error && <span className="text-red-400">{error}</span>}
                {!error && dirty && <span className="text-amber-400">Unsaved changes</span>}
                {!error && !dirty && <span className="text-slate-600">Saved</span>}
              </>
            )}
          </div>
          <div className="flex items-center gap-2">
            {section !== 'tools' && !READ_ONLY_SECTIONS.includes(section) && (
              <Button
                variant="outline"
                size="sm"
                onClick={handleResetSection}
                className="border-slate-600 text-slate-400 hover:text-slate-100 hover:bg-slate-800 gap-1"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                Reset this section
              </Button>
            )}
            {!READ_ONLY_SECTIONS.includes(section) && (
              <Button
                size="sm"
                disabled={!dirty || saving}
                onClick={handleSave}
                className="bg-cyan-600 hover:bg-cyan-500 text-white"
              >
                {saving ? 'Saving…' : 'Save defaults'}
              </Button>
            )}
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
};

export default AppSetupPanel;
