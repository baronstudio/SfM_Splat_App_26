import React from 'react';
import { Info, RotateCcw } from 'lucide-react';
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
import type { TrainDefaults, TrainPreset } from '@/types';

/**
 * Step 4's Advanced panel — layer 3 of the settings model (CLAUDE.md §4).
 *
 * The panel's defaults follow the *selected preset*, not a frozen copy of
 * `3dgs`'s. The preset is the first positional argument of `spirula train` and
 * it moves the defaults of everything under it, so a panel showing `3dgs`'s
 * numbers while `meshing` is selected would be lying about `--primitive`,
 * `--sh-degree` and `--background-mode` at once (§12, 2026-08-27).
 *
 * Every knob therefore has a third position beyond its values: **unset**, drawn
 * as "preset decides", which is `null` in the stored settings and nothing at
 * all on the command line. Naming a flag overrides the preset, so leaving a
 * knob alone is the only way to let the preset keep deciding it — the same rule
 * `SfmSettings` follows against the build's own defaults, except that here the
 * baseline moves with the preset.
 *
 * `PRESET_DEFAULTS` mirrors `_PRESET_DEFAULTS` in `step_train.py`, and both are
 * read off `docs/spirula/train-help-all-<preset>.txt`.
 */
interface TrainSettingsProps {
  settings: TrainDefaults;
  /** How many masks sit in `masks/` — what the mask row actually affects. */
  maskCount?: number;
  /** Whether `sfm/depths` and `sfm/normals` hold anything (§7.5). */
  hasDepths?: boolean;
  hasNormals?: boolean;
  onChange: (s: TrainDefaults) => void;
}

/** The flags every preset agrees on. */
const PRESET_BASE = {
  num_iterations: 30000,
  quality: 'medium',
  cap_max: 1000000,
  sh_degree: 3,
  primitive: '3dgs',
  background_mode: 'black',
  steps_per_save: 2000,
  save_only_latest_checkpoint: true,
  save_eval_images: false,
  distraction_robustness: 'off',
  floater_suppression: 'off',
  mask_boundary_offset: 0,
  depth_supervision_weight: 0,
  normal_supervision_weight: 0.01,
  orientation_method: 'up',
  center_method: 'poses',
  auto_scale_poses: true,
  train_frame: 'points',
} as const;

/** What each preset moves. Empty means "this preset is the base". */
const PRESET_DELTAS: Record<TrainPreset, Partial<Record<keyof typeof PRESET_BASE, unknown>>> = {
  '3dgs': {},
  '360-camera': { primitive: 'mip', mask_boundary_offset: -0.025 },
  'in-the-wild': {
    distraction_robustness: 'strong',
    center_method: 'focus',
    mask_boundary_offset: -0.025,
  },
  'linear-color': { background_mode: 'noise' },
  synthetic: {},
  meshing: { primitive: '3dgut', sh_degree: 0, background_mode: 'noise' },
  'academic-baseline': {
    normal_supervision_weight: 0,
    orientation_method: 'gsplat',
    center_method: 'gsplat',
  },
};

const PRESET_NOTES: Record<TrainPreset, string> = {
  '3dgs': 'Generic method that works well for most datasets.',
  '360-camera':
    'Original distorted images from a 360 camera, with the lens circle visible. '
    + 'Mip primitive, and the masks pulled in by 2.5 % of the image size.',
  'in-the-wild':
    'Internet images: extreme lighting variation, un-masked outliers, long focal '
    + 'lengths. Distraction robustness on strong.',
  'linear-color': 'Splats trained in a linear colour space such as ACEScg.',
  synthetic: 'Rendered datasets at constant exposure.',
  meshing:
    'Aimed at the quality of the mesh geometry rather than at how the splats '
    + 'look: 3dgut primitive, no view-dependent colour, noise background. '
    + 'The preset to select before step 5.',
  'academic-baseline':
    'Not listed by --help and it works (measured 2026-08-27). gsplat orientation '
    + 'and centring, no geometry supervision — the comparable-numbers preset.',
};

export function presetDefaults(preset: TrainPreset): Record<string, unknown> {
  return { ...PRESET_BASE, ...PRESET_DELTAS[preset] };
}

const Row: React.FC<{
  label: string;
  /** Shown to the right: the preset's value, or that this knob is explicit. */
  state: string;
  explicit: boolean;
  onClear: () => void;
  hint?: string;
  children: React.ReactNode;
}> = ({ label, state, explicit, onClear, hint, children }) => (
  <div className="space-y-2">
    <div className="flex items-center justify-between gap-2">
      <Label>{label}</Label>
      {explicit ? (
        <button
          type="button"
          onClick={onClear}
          className="flex items-center gap-1 text-xs font-mono text-cyan-400 hover:text-cyan-300"
          title="Hand this knob back to the preset — it leaves the command line entirely."
        >
          <RotateCcw className="w-3 h-3" />
          explicit
        </button>
      ) : (
        <span className="text-xs font-mono text-slate-500">{state}</span>
      )}
    </div>
    {children}
    {hint && <p className="text-xs text-slate-500">{hint}</p>}
  </div>
);

/** A select whose first entry hands the knob back to the preset. */
const Choice = <T extends string>({
  value, presetValue, onChange, options,
}: {
  value: T | null;
  presetValue: string;
  onChange: (v: T | null) => void;
  options: { value: T; label: string }[];
}) => (
  <Select
    value={value ?? '__preset__'}
    onValueChange={(v) => onChange(v === '__preset__' ? null : (v as T))}
  >
    <SelectTrigger><SelectValue /></SelectTrigger>
    <SelectContent>
      <SelectItem value="__preset__">Preset decides ({presetValue})</SelectItem>
      {options.map((o) => (
        <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
      ))}
    </SelectContent>
  </Select>
);

/** A number field that shows the preset's value until somebody types one. */
const Num: React.FC<{
  value: number | null;
  presetValue: number;
  min?: number;
  step?: number;
  onChange: (v: number | null) => void;
}> = ({ value, presetValue, min, step, onChange }) => (
  <Input
    type="number"
    min={min}
    step={step}
    value={value ?? ''}
    placeholder={String(presetValue)}
    onChange={(e) => {
      const raw = e.target.value.trim();
      onChange(raw === '' ? null : Number(raw));
    }}
  />
);

const TrainSettings: React.FC<TrainSettingsProps> = ({
  settings, maskCount = 0, hasDepths = false, hasNormals = false, onChange,
}) => {
  const update = <K extends keyof TrainDefaults>(key: K, value: TrainDefaults[K]) => {
    onChange({ ...settings, [key]: value });
  };

  const preset = presetDefaults(settings.preset);
  const explicitCount = (Object.keys(PRESET_BASE) as (keyof typeof PRESET_BASE)[])
    .filter((key) => settings[key as keyof TrainDefaults] !== null
      && settings[key as keyof TrainDefaults] !== undefined).length;

  const num = (key: keyof typeof PRESET_BASE) => preset[key] as number;
  const str = (key: keyof typeof PRESET_BASE) => String(preset[key]);

  return (
    <div className="space-y-6">
      <div>
        <Label>Preset</Label>
        <p className="text-xs text-slate-500">
          The first positional argument, and it moves the defaults of everything
          below it.
        </p>
      </div>

      <Row
        label="Training preset"
        state=""
        explicit={false}
        onClear={() => {}}
        hint={PRESET_NOTES[settings.preset]}
      >
        <Select
          value={settings.preset}
          onValueChange={(v) => update('preset', v as TrainPreset)}
        >
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="3dgs">3dgs (default)</SelectItem>
            <SelectItem value="360-camera">360-camera</SelectItem>
            <SelectItem value="in-the-wild">in-the-wild</SelectItem>
            <SelectItem value="linear-color">linear-color</SelectItem>
            <SelectItem value="synthetic">synthetic</SelectItem>
            <SelectItem value="meshing">meshing</SelectItem>
            <SelectItem value="academic-baseline">academic-baseline</SelectItem>
          </SelectContent>
        </Select>
      </Row>

      <Separator className="bg-slate-700/50" />

      <div>
        <div className="flex items-center justify-between">
          <Label>Overrides</Label>
          <span className="text-xs font-mono text-slate-500">
            {explicitCount === 0
              ? 'none — the preset decides'
              : `${explicitCount} sent explicitly`}
          </span>
        </div>
        <p className="text-xs text-slate-500 mt-1 flex gap-1.5">
          <Info className="w-3.5 h-3.5 mt-0.5 shrink-0 text-slate-600" />
          Each of these reaches the command line only once you set it, because
          naming a flag overrides what the preset set it to. Leaving one alone is
          what lets the preset keep deciding — and the value shown beside it is
          the one this preset will really use.
        </p>
      </div>

      <Row
        label="Iterations"
        state={`preset: ${num('num_iterations').toLocaleString()}`}
        explicit={settings.num_iterations !== null}
        onClear={() => update('num_iterations', null)}
        hint="How long training runs. 30 000 is every preset's default; a first look at a scene is worth far less."
      >
        <Num
          value={settings.num_iterations}
          presetValue={num('num_iterations')}
          min={100}
          step={1000}
          onChange={(v) => update('num_iterations', v)}
        />
      </Row>

      <Row
        label="Quality"
        state={`preset: ${str('quality')}`}
        explicit={settings.quality !== null}
        onClear={() => update('quality', null)}
        hint="Overall detail level: the splat budget and how long training runs."
      >
        <Choice
          value={settings.quality}
          presetValue={str('quality')}
          onChange={(v) => update('quality', v)}
          options={[
            { value: 'low', label: 'Low' },
            { value: 'medium', label: 'Medium' },
            { value: 'high', label: 'High' },
            { value: 'ultra', label: 'Ultra' },
          ]}
        />
      </Row>

      <Row
        label="Max splats"
        state={`preset: ${num('cap_max').toLocaleString()}`}
        explicit={settings.cap_max !== null}
        onClear={() => update('cap_max', null)}
        hint="Largest number of splats the scene may grow to. The reference 7 000-iteration run reached 998 463 and its splat.ply was 247 MB."
      >
        <Num
          value={settings.cap_max}
          presetValue={num('cap_max')}
          min={10000}
          step={100000}
          onChange={(v) => update('cap_max', v)}
        />
      </Row>

      <Row
        label="Primitive"
        state={`preset: ${str('primitive')}`}
        explicit={settings.primitive !== null}
        onClear={() => update('primitive', null)}
        hint="Shape used for each splat. 360-camera presets mip; meshing presets 3dgut."
      >
        <Choice
          value={settings.primitive}
          presetValue={str('primitive')}
          onChange={(v) => update('primitive', v)}
          options={[
            { value: '3dgs', label: '3dgs' },
            { value: 'mip', label: 'mip' },
            { value: '3dgut', label: '3dgut' },
          ]}
        />
      </Row>

      <Row
        label="SH degree"
        state={`preset: ${num('sh_degree')}`}
        explicit={settings.sh_degree !== null}
        onClear={() => update('sh_degree', null)}
        hint="How much a splat's colour may change with viewing angle. 0 is flat colour, which is what the meshing preset wants."
      >
        <Num
          value={settings.sh_degree}
          presetValue={num('sh_degree')}
          min={0}
          step={1}
          onChange={(v) => update('sh_degree', v)}
        />
      </Row>

      <Row
        label="Background"
        state={`preset: ${str('background_mode')}`}
        explicit={settings.background_mode !== null}
        onClear={() => update('background_mode', null)}
        hint="What fills pixels no splat covers. noise stops a solid backdrop being learnt behind the subject."
      >
        <Choice
          value={settings.background_mode}
          presetValue={str('background_mode')}
          onChange={(v) => update('background_mode', v)}
          options={[
            { value: 'black', label: 'Black' },
            { value: 'noise', label: 'Noise' },
            { value: 'sh', label: 'Learned skybox (sh)' },
          ]}
        />
      </Row>

      <Row
        label="Distraction robustness"
        state={`preset: ${str('distraction_robustness')}`}
        explicit={settings.distraction_robustness !== null}
        onClear={() => update('distraction_robustness', null)}
        hint="Ignore people, cars and anything else that moves between photos — without a mask run. in-the-wild presets it to strong."
      >
        <Choice
          value={settings.distraction_robustness}
          presetValue={str('distraction_robustness')}
          onChange={(v) => update('distraction_robustness', v)}
          options={[
            { value: 'off', label: 'Off' },
            { value: 'mild', label: 'Mild' },
            { value: 'strong', label: 'Strong' },
          ]}
        />
      </Row>

      <Row
        label="Floater suppression"
        state={`preset: ${str('floater_suppression')}`}
        explicit={settings.floater_suppression !== null}
        onClear={() => update('floater_suppression', null)}
        hint="Clean up floating blobs and see-through surfaces."
      >
        <Choice
          value={settings.floater_suppression}
          presetValue={str('floater_suppression')}
          onChange={(v) => update('floater_suppression', v)}
          options={[
            { value: 'off', label: 'Off' },
            { value: 'mild', label: 'Mild' },
            { value: 'strong', label: 'Strong' },
          ]}
        />
      </Row>

      <Separator className="bg-slate-700/50" />

      <div className="flex items-start justify-between gap-6">
        <div className="min-w-0">
          <Label>Use masks</Label>
          <p className="text-xs text-slate-500">
            {maskCount > 0
              ? `${maskCount} mask(s) in masks/. They are trained as empty space `
                + '(--apply-loss-for-mask 1), which removes the background and '
                + 'leaves the subject.'
              : 'masks/ is empty, so this changes nothing yet — step 2’s alpha '
                + 'extraction or a spirula sam run fills it.'}
            {' '}The other position, “ignore”, is not offered: it drops the
            masked pixels from the loss without deleting anything, and measured
            as indistinguishable from no masks at all.
          </p>
        </div>
        <Switch
          checked={settings.load_masks}
          onCheckedChange={(v) => update('load_masks', v)}
        />
      </div>

      <Row
        label="Mask boundary offset"
        state={`preset: ${num('mask_boundary_offset')}`}
        explicit={settings.mask_boundary_offset !== null}
        onClear={() => update('mask_boundary_offset', null)}
        hint="Signed: grows or shrinks every mask by this fraction of the image size. 360-camera and in-the-wild preset it to -0.025 to lose the lens border."
      >
        <Num
          value={settings.mask_boundary_offset}
          presetValue={num('mask_boundary_offset')}
          step={0.005}
          onChange={(v) => update('mask_boundary_offset', v)}
        />
      </Row>

      <Separator className="bg-slate-700/50" />

      <div>
        <Label>Geometry supervision</Label>
        <p className="text-xs text-slate-500 mt-1">
          Per-image depth and normal maps written by the geometry panel into
          sfm/depths and sfm/normals. Both sit inside the dataset folder, so the
          trainer finds them by name and they cost no flag — but a run sends 0
          for whichever directory is empty rather than pointing the trainer at
          one that is not there.
        </p>
      </div>

      <div className="flex items-start justify-between gap-6">
        <div className="min-w-0">
          <Label>Use normal maps</Label>
          <p className="text-xs text-slate-500">
            {hasNormals ? 'sfm/normals is populated.' : 'sfm/normals is empty.'}
            {' '}The normals are what a reconstruction usually wants.
          </p>
        </div>
        <Switch
          checked={settings.load_normals}
          onCheckedChange={(v) => update('load_normals', v)}
        />
      </div>

      <div className="flex items-start justify-between gap-6">
        <div className="min-w-0">
          <Label>Use depth maps</Label>
          <p className="text-xs text-slate-500">
            {hasDepths ? 'sfm/depths is populated.' : 'sfm/depths is empty.'}
            {' '}Depth is off in the geometry tool’s own default: it doubles both
            the time on disk and the reading a training run does.
          </p>
        </div>
        <Switch
          checked={settings.load_depths}
          onCheckedChange={(v) => update('load_depths', v)}
        />
      </div>

      <Row
        label="Normal supervision weight"
        state={`preset: ${num('normal_supervision_weight')}`}
        explicit={settings.normal_supervision_weight !== null}
        onClear={() => update('normal_supervision_weight', null)}
        hint="How strongly the AI-predicted surface direction guides the geometry. academic-baseline presets it to 0."
      >
        <Num
          value={settings.normal_supervision_weight}
          presetValue={num('normal_supervision_weight')}
          min={0}
          step={0.01}
          onChange={(v) => update('normal_supervision_weight', v)}
        />
      </Row>

      <Row
        label="Depth supervision weight"
        state={`preset: ${num('depth_supervision_weight')}`}
        explicit={settings.depth_supervision_weight !== null}
        onClear={() => update('depth_supervision_weight', null)}
        hint="How strongly AI-predicted depth guides the geometry. 0 in every preset."
      >
        <Num
          value={settings.depth_supervision_weight}
          presetValue={num('depth_supervision_weight')}
          min={0}
          step={0.01}
          onChange={(v) => update('depth_supervision_weight', v)}
        />
      </Row>

      <Separator className="bg-slate-700/50" />

      <div>
        <Label>Scene placement</Label>
        <p className="text-xs text-slate-500 mt-1">
          The trained splat comes out in the same frame as the sparse cloud it
          was seeded from — measured at 90.1 % occupancy overlap at identity, +Z
          up — and the viewer applies one rotation to everything. Changing these
          changes that, so the viewer’s reading of the scene changes with them.
        </p>
      </div>

      <Row
        label="Orientation"
        state={`preset: ${str('orientation_method')}`}
        explicit={settings.orientation_method !== null}
        onClear={() => update('orientation_method', null)}
        hint="How the scene is levelled. The mapper already levels on the cameras at step 3."
      >
        <Choice
          value={settings.orientation_method}
          presetValue={str('orientation_method')}
          onChange={(v) => update('orientation_method', v)}
          options={[
            { value: 'up', label: 'up' },
            { value: 'pca', label: 'pca' },
            { value: 'vertical', label: 'vertical' },
            { value: 'gsplat', label: 'gsplat' },
            { value: 'none', label: 'none' },
          ]}
        />
      </Row>

      <Row
        label="Centring"
        state={`preset: ${str('center_method')}`}
        explicit={settings.center_method !== null}
        onClear={() => update('center_method', null)}
        hint="What the scene is centred on. in-the-wild presets focus; academic-baseline presets gsplat."
      >
        <Choice
          value={settings.center_method}
          presetValue={str('center_method')}
          onChange={(v) => update('center_method', v)}
          options={[
            { value: 'poses', label: 'poses' },
            { value: 'focus', label: 'focus' },
            { value: 'gsplat', label: 'gsplat' },
            { value: 'none', label: 'none' },
          ]}
        />
      </Row>

      <Row
        label="Train frame"
        state={`preset: ${str('train_frame')}`}
        explicit={settings.train_frame !== null}
        onClear={() => update('train_frame', null)}
        hint="points keeps the splats in the raw dataset frame, which is what makes the viewer's single scene-root rotation correct for the cloud and the splat alike. Moving it moves the splat out of the sparse cloud's frame."
      >
        <Choice
          value={settings.train_frame}
          presetValue={str('train_frame')}
          onChange={(v) => update('train_frame', v)}
          options={[
            { value: 'points', label: 'points (default)' },
            { value: 'normalized', label: 'normalized' },
            { value: 'camera', label: 'camera' },
          ]}
        />
      </Row>

      <Separator className="bg-slate-700/50" />

      <Row
        label="Steps per checkpoint"
        state={`preset: ${num('steps_per_save')}`}
        explicit={settings.steps_per_save !== null}
        onClear={() => update('steps_per_save', null)}
        hint="Only the newest checkpoint survives a run by default, so this is how far back a crash can be recovered from, not how many splats you end up with."
      >
        <Num
          value={settings.steps_per_save}
          presetValue={num('steps_per_save')}
          min={100}
          step={500}
          onChange={(v) => update('steps_per_save', v)}
        />
      </Row>
    </div>
  );
};

export default TrainSettings;
