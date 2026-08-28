import React from 'react';
import { Info } from 'lucide-react';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Separator } from '@/components/ui/separator';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import type { SfmDefaults } from '@/types';

/**
 * Step 3's Advanced panel — layer 3 of the settings model (CLAUDE.md §4).
 *
 * `spirula sfm auto` deliberately exposes two headline knobs, `--quality` and
 * `--data-type`, which set the working resolution, the feature budget and the
 * pair selection, and the run reports what they moved (§7.1). Everything under
 * "Overrides" is left off the command line while it equals the build's own
 * default, because naming a flag explicitly *overrides the preset* — so sending
 * `--max-features 8192` on the grounds that it is also the default would
 * silently undo `--quality medium`'s drop to 4096. The panel says so where the
 * user can see it rather than burying it in `step_sfm.py`.
 */
interface SfmSettingsProps {
  settings: SfmDefaults;
  /** How many masks sit in `masks/` — what "Use masks" actually affects today. */
  maskCount?: number;
  onChange: (s: SfmDefaults) => void;
}

/** The values the installed build prints as its own defaults. Mirrors
 *  `_BUILD_DEFAULTS` in `step_sfm.py`, which decides what is really sent. */
const BUILD_DEFAULTS = {
  pairs: 'auto',
  camera_model: 'opencv',
  camera_mode: 'folder',
  max_image_size: 0,
  max_features: 8192,
} as const;

const Row: React.FC<{
  label: string;
  value?: string;
  hint?: string;
  children: React.ReactNode;
}> = ({ label, value, hint, children }) => (
  <div className="space-y-2">
    <div className="flex items-center justify-between">
      <Label>{label}</Label>
      {value && <span className="text-xs text-cyan-400 font-mono">{value}</span>}
    </div>
    {children}
    {hint && <p className="text-xs text-slate-500">{hint}</p>}
  </div>
);

const Choice = <T extends string>({
  value, onChange, options,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
}) => (
  <Select value={value} onValueChange={(v) => onChange(v as T)}>
    <SelectTrigger><SelectValue /></SelectTrigger>
    <SelectContent>
      {options.map((o) => (
        <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
      ))}
    </SelectContent>
  </Select>
);

const SfmSettings: React.FC<SfmSettingsProps> = ({ settings, maskCount = 0, onChange }) => {
  const update = <K extends keyof SfmDefaults>(key: K, value: SfmDefaults[K]) => {
    onChange({ ...settings, [key]: value });
  };

  const overridden = (Object.keys(BUILD_DEFAULTS) as (keyof typeof BUILD_DEFAULTS)[])
    .filter((key) => settings[key] !== BUILD_DEFAULTS[key]);

  return (
    <div className="space-y-6">
      <div>
        <Label>Reconstruction</Label>
        <p className="text-xs text-slate-500">
          Two knobs decide the rest, and the run reports what they moved.
        </p>
      </div>

      <Row
        label="Quality"
        hint="Working resolution, feature budget and pair-selection breadth. high is the build's default; medium reconstructed 251/251 images at 0.50 px mean reprojection in 34.6 s on this workstation."
      >
        <Choice
          value={settings.quality}
          onChange={(v) => update('quality', v)}
          options={[
            { value: 'low', label: 'Low' },
            { value: 'medium', label: 'Medium' },
            { value: 'high', label: 'High (default)' },
            { value: 'extreme', label: 'Extreme' },
          ]}
        />
      </Row>

      <Row
        label="Data type"
        hint="What the capture is. video switches pair selection to sequential plus loop closure — which is what a project that came through step 2's frame extraction is."
      >
        <Choice
          value={settings.data_type}
          onChange={(v) => update('data_type', v)}
          options={[
            { value: 'individual', label: 'Individual photos' },
            { value: 'video', label: 'Video frames' },
            { value: 'internet', label: 'Internet collection' },
          ]}
        />
      </Row>

      <Row
        label="Camera model"
        hint="The lens model fitted to each camera group. spirula reads equirectangular and >180° fisheye natively, so a 360 rig needs no undistortion pass anywhere in this pipeline."
      >
        <Choice
          value={settings.camera_model}
          onChange={(v) => update('camera_model', v)}
          options={[
            { value: 'simple-pinhole', label: 'Simple pinhole' },
            { value: 'pinhole', label: 'Pinhole' },
            { value: 'radial', label: 'Radial' },
            { value: 'opencv', label: 'OpenCV (default)' },
            { value: 'full-opencv', label: 'Full OpenCV' },
            { value: 'opencv-fisheye', label: 'OpenCV fisheye' },
            { value: 'thin-prism-fisheye', label: 'Thin-prism fisheye' },
            { value: 'equirectangular', label: 'Equirectangular (360°)' },
          ]}
        />
      </Row>

      <Separator className="bg-slate-700/50" />

      <div>
        <div className="flex items-center justify-between">
          <Label>Overrides</Label>
          <span className="text-xs font-mono text-slate-500">
            {overridden.length === 0
              ? 'none — the presets decide'
              : `${overridden.length} sent explicitly`}
          </span>
        </div>
        <p className="text-xs text-slate-500 mt-1 flex gap-1.5">
          <Info className="w-3.5 h-3.5 mt-0.5 shrink-0 text-slate-600" />
          Each of these is put on the command line only while it differs from the
          build's own default. Naming a flag overrides what Quality and Data type
          set it to, so leaving one alone is what lets the preset keep deciding
          it.
        </p>
      </div>

      <Row
        label="Pairs"
        hint="Which image pairs are matched. auto is GPU pair selection at 100 images or more, sequential (plus loop closure) for video below that, and exhaustive otherwise."
      >
        <Choice
          value={settings.pairs}
          onChange={(v) => update('pairs', v)}
          options={[
            { value: 'auto', label: 'Auto (default)' },
            { value: 'exhaustive', label: 'Exhaustive' },
            { value: 'sequential', label: 'Sequential' },
            { value: 'prefilter', label: 'Prefilter' },
          ]}
        />
      </Row>

      <Row
        label="Camera grouping"
        hint="How images are grouped into cameras. Every mode splits on image resolution first — which is why one folder can report two cameras. Those are two intrinsics, not two reconstructions."
      >
        <Choice
          value={settings.camera_mode}
          onChange={(v) => update('camera_mode', v)}
          options={[
            { value: 'single', label: 'Single' },
            { value: 'folder', label: 'Folder (default)' },
            { value: 'image', label: 'Per image' },
          ]}
        />
      </Row>

      <Row
        label="Max image size"
        value={settings.max_image_size === 0 ? 'preset decides' : `${settings.max_image_size} px`}
        hint="Longest edge the extractor runs on; keypoints are still reported in the source image's pixels. 0 leaves it to Quality — which sets 1600 at medium on this build."
      >
        <Input
          type="number"
          min={0}
          step={200}
          value={settings.max_image_size}
          onChange={(e) => update('max_image_size', Number(e.target.value) || 0)}
        />
      </Row>

      <Row
        label="Max features"
        value={settings.max_features === BUILD_DEFAULTS.max_features ? 'preset decides' : 'explicit'}
        hint="Keypoints kept per image, largest scales first. 8192 is the build default, and Quality medium lowers it to 4096 on its own."
      >
        <Input
          type="number"
          min={256}
          step={1024}
          value={settings.max_features}
          onChange={(e) => update('max_features', Number(e.target.value) || BUILD_DEFAULTS.max_features)}
        />
      </Row>

      <Separator className="bg-slate-700/50" />

      <div className="flex items-start justify-between gap-6">
        <div className="min-w-0">
          <Label>Use masks</Label>
          <p className="text-xs text-slate-500">
            {maskCount > 0
              ? `${maskCount} mask(s) in masks/. Keypoints on black pixels are dropped.`
              : 'masks/ is empty, so this changes nothing yet — step 2’s alpha extraction or a spirula sam run fills it.'}
            {' '}masks/ is already a sibling of frames/, which is the layout
            sfm auto adopts by itself: only refusing them costs a flag
            (--no-masks).
          </p>
        </div>
        <Switch
          checked={settings.use_masks}
          onCheckedChange={(v) => update('use_masks', v)}
        />
      </div>

      <div className="flex items-start justify-between gap-6">
        <div className="min-w-0">
          <Label>Write progress snapshots</Label>
          <p className="text-xs text-slate-500">
            --progress-dir writes model.bin and pairs.bin while the run goes, for
            a front end that shows the reconstruction assembling rather than
            tailing its log. Nothing reads them yet, and they cost disk.
          </p>
        </div>
        <Switch
          checked={settings.progress_dir}
          onCheckedChange={(v) => update('progress_dir', v)}
        />
      </div>
    </div>
  );
};

export default SfmSettings;
