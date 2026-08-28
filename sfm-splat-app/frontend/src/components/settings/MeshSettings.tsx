import React from 'react';
import { AlertTriangle, Info } from 'lucide-react';
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
import type { MeshColor, MeshDefaults, MeshFormat } from '@/types';

/**
 * Step 5's Advanced panel — layer 3 of the settings model (CLAUDE.md §4).
 *
 * `spirula mesh` has no presets, so unlike `TrainSettings` this panel shows real
 * numbers rather than "the preset decides": every knob here holds a value, and
 * `step_mesh._moved_from_build_default` keeps the untouched ones off the command
 * line so the log line stays readable.
 *
 * The one thing this panel *enforces* is the format/colour pair. PLY carries no
 * texture and OBJ carries no vertex colours, and the tool does not skip the
 * offending format — measured, `--format glb,ply --color texture` exited **1
 * having written nothing at all, not even the glb** (§12, 2026-08-27). So the
 * refusal is shown here and the Run button is disabled on it, rather than found
 * out after the run.
 */

/** Mirrors `_BUILD_DEFAULTS` in `step_mesh.py` — what the build does on its own. */
export const MESH_BUILD_DEFAULTS = {
  texture_size: 0,
  max_cameras: -1,
  max_grid_res: 512,
  cull_unseen: true,
  floater_min_faces: 100,
  quality_iters: 3,
  num_threads: 0,
} as const;

const FORMATS: { value: MeshFormat; label: string; note: string }[] = [
  { value: 'glb', label: 'GLB', note: 'glTF binary — texture inside the file. The one the viewer reads.' },
  { value: 'gltf', label: 'glTF', note: 'glTF JSON, texture beside it.' },
  { value: 'obj', label: 'OBJ', note: 'Texture only — OBJ carries no vertex colours.' },
  { value: 'ply', label: 'PLY', note: 'Vertex colour only — PLY carries no texture.' },
];

/**
 * The refusal message for an impossible format/colour pair, or null.
 * Mirrors `step_mesh.check_formats`, which is the half a run started from
 * anywhere else still hits.
 */
export function meshRefusal(settings: MeshDefaults): string | null {
  if (settings.formats.length === 0) {
    return 'Pick at least one output format.';
  }
  if (settings.formats.includes('ply') && settings.color === 'texture') {
    return 'PLY carries no texture. Drop PLY, or set the colour to vertex or none.';
  }
  if (settings.formats.includes('obj') && settings.color === 'vertex') {
    return 'OBJ carries no vertex colours. Drop OBJ, or set the colour to texture or none.';
  }
  return null;
}

interface MeshSettingsProps {
  settings: MeshDefaults;
  /** Cameras in the reconstruction, for the "Use the cameras" copy. */
  cameraCount?: number;
  onChange: (s: MeshDefaults) => void;
}

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

const MeshSettings: React.FC<MeshSettingsProps> = ({
  settings, cameraCount = 0, onChange,
}) => {
  const update = <K extends keyof MeshDefaults>(key: K, value: MeshDefaults[K]) => {
    onChange({ ...settings, [key]: value });
  };

  const toggleFormat = (fmt: MeshFormat) => {
    const next = settings.formats.includes(fmt)
      ? settings.formats.filter((f) => f !== fmt)
      : [...settings.formats, fmt];
    // Kept in the order of FORMATS rather than of clicks, so the command line
    // reads the same whichever order the boxes were ticked in.
    update('formats', FORMATS.map((f) => f.value).filter((f) => next.includes(f)));
  };

  const refusal = meshRefusal(settings);
  const overridden = (Object.keys(MESH_BUILD_DEFAULTS) as (keyof typeof MESH_BUILD_DEFAULTS)[])
    .filter((key) => settings[key] !== MESH_BUILD_DEFAULTS[key]);

  return (
    <div className="space-y-6">
      <div>
        <Label>Output</Label>
        <p className="text-xs text-slate-500">
          What is written to <span className="font-mono">mesh/</span>, and how
          it is coloured.
        </p>
      </div>

      <div className="space-y-2">
        <Label>Formats</Label>
        <div className="grid grid-cols-2 gap-2">
          {FORMATS.map((f) => {
            const on = settings.formats.includes(f.value);
            return (
              <button
                key={f.value}
                type="button"
                onClick={() => toggleFormat(f.value)}
                title={f.note}
                className={`rounded-md border px-3 py-2 text-left text-sm transition-colors ${
                  on
                    ? 'border-cyan-600 bg-cyan-950/40 text-slate-100'
                    : 'border-slate-700 bg-slate-900/40 text-slate-400 hover:text-slate-200'
                }`}
              >
                <span className="font-medium">{f.label}</span>
                <span className="block text-[11px] text-slate-500">{f.note}</span>
              </button>
            );
          })}
        </div>
      </div>

      <Row
        label="Colour"
        hint="none leaves bare geometry; vertex writes a colour per vertex; texture bakes an atlas from the camera renders and is what a glb is worth having."
      >
        <Choice
          value={settings.color}
          onChange={(v: MeshColor) => update('color', v)}
          options={[
            { value: 'none', label: 'None' },
            { value: 'vertex', label: 'Vertex colour (default)' },
            { value: 'texture', label: 'Texture atlas' },
          ]}
        />
      </Row>

      {refusal && (
        <p className="flex gap-2 text-xs text-red-300 bg-red-950/30 border border-red-800 rounded px-3 py-2">
          <AlertTriangle className="w-4 h-4 mt-px shrink-0" />
          <span>
            {refusal}{' '}
            <span className="text-red-400/80">
              The tool refuses this pair and exits having written nothing at
              all — not even the formats it could have made — so the run is
              blocked here instead.
            </span>
          </span>
        </p>
      )}

      {settings.color === 'texture' && (
        <>
          <Row
            label="Texture encoding"
            hint="GLB only, and it is the file inside the glb: PNG is lossless and what the build does on its own; JPEG q95 and q75 trade it for size."
          >
            <Choice
              value={settings.texture_encoding}
              onChange={(v) => update('texture_encoding', v)}
              options={[
                { value: 'png', label: 'PNG (default)' },
                { value: 'jpg', label: 'JPEG q95' },
                { value: 'jpeg75', label: 'JPEG q75' },
              ]}
            />
          </Row>

          <Row
            label="Texture size"
            value={settings.texture_size === 0
              ? 'from the texel budget'
              : `${settings.texture_size} px`}
            hint="Atlas resolution. 0 lets the run pick it from the observed detail — on the reference mesh it chose 4096 and filled 26.8 % of it."
          >
            <Input
              type="number"
              min={0}
              step={1024}
              value={settings.texture_size}
              onChange={(e) => update('texture_size', Number(e.target.value) || 0)}
            />
          </Row>
        </>
      )}

      <Separator className="bg-slate-700/50" />

      <div className="flex items-start justify-between gap-6">
        <div className="min-w-0">
          <Label>Use the cameras</Label>
          <p className="text-xs text-slate-500">
            The reconstruction&rsquo;s cameras decide occupancy and colour
            {cameraCount ? ` — ${cameraCount} of them` : ''}. Off sends{' '}
            <span className="font-mono">--no-data</span> and meshes from the
            gaussian densities alone, which also drops the isosurface default
            from 0.5 to 0.2.
          </p>
        </div>
        <Switch
          checked={settings.use_cameras}
          onCheckedChange={(v) => update('use_cameras', v)}
        />
      </div>

      <Row
        label="Isosurface level"
        value={settings.iso === 0 ? 'build decides' : settings.iso.toFixed(2)}
        hint="Where the surface is cut out of the occupancy field. 0 leaves it to the build, and that is not one number: 0.5 with cameras, 0.2 without. Higher tightens the surface onto dense gaussians."
      >
        <Input
          type="number"
          min={0}
          max={1}
          step={0.05}
          value={settings.iso}
          onChange={(e) => update('iso', Number(e.target.value) || 0)}
        />
      </Row>

      <Separator className="bg-slate-700/50" />

      <div>
        <div className="flex items-center justify-between">
          <Label>Overrides</Label>
          <span className="text-xs font-mono text-slate-500">
            {overridden.length === 0
              ? 'none — the build decides'
              : `${overridden.length} sent explicitly`}
          </span>
        </div>
        <p className="text-xs text-slate-500 mt-1 flex gap-1.5">
          <Info className="w-3.5 h-3.5 mt-0.5 shrink-0 text-slate-600" />
          Each of these goes on the command line only while it differs from the
          build&rsquo;s own default. <span className="font-mono">mesh</span> has
          no presets, so nothing is undone by naming one — it only keeps the
          command line in the log readable.
        </p>
      </div>

      <Row
        label="Max cameras"
        value={settings.max_cameras < 0 ? 'all of them' : String(settings.max_cameras)}
        hint="Cap on the cameras used for occupancy and colour, picked as k-means medoids. -1 uses all of them; lowering it is the first thing to try on a capture of thousands."
      >
        <Input
          type="number"
          min={-1}
          step={10}
          value={settings.max_cameras}
          onChange={(e) => update('max_cameras', Number(e.target.value))}
        />
      </Row>

      <Row
        label="Max grid resolution"
        value={`${settings.max_grid_res}`}
        hint="Cap on the acceleration grid. Raising it buys detail and costs memory across the whole occupancy pass."
      >
        <Input
          type="number"
          min={64}
          step={64}
          value={settings.max_grid_res}
          onChange={(e) => update('max_grid_res', Number(e.target.value) || MESH_BUILD_DEFAULTS.max_grid_res)}
        />
      </Row>

      <Row
        label="Floater minimum faces"
        value={`${settings.floater_min_faces}`}
        hint="Components smaller than this are dropped. The reference mesh still ended on 5111 components, so this is a floor rather than a cleanup."
      >
        <Input
          type="number"
          min={0}
          step={10}
          value={settings.floater_min_faces}
          onChange={(e) => update('floater_min_faces', Number(e.target.value) || 0)}
        />
      </Row>

      <Row
        label="Quality iterations"
        value={`${settings.quality_iters}`}
        hint="Valence-flip and tangential-relaxation passes over the finished surface."
      >
        <Input
          type="number"
          min={0}
          max={20}
          step={1}
          value={settings.quality_iters}
          onChange={(e) => update('quality_iters', Number(e.target.value) || 0)}
        />
      </Row>

      <Row
        label="Threads"
        value={settings.num_threads === 0 ? 'every hardware thread' : String(settings.num_threads)}
        hint="0 uses every hardware thread — 12 on this workstation, which is what the Delaunay phase reports."
      >
        <Input
          type="number"
          min={0}
          step={1}
          value={settings.num_threads}
          onChange={(e) => update('num_threads', Number(e.target.value) || 0)}
        />
      </Row>

      <div className="flex items-start justify-between gap-6">
        <div className="min-w-0">
          <Label>Drop unseen vertices</Label>
          <p className="text-xs text-slate-500">
            Vertices no camera saw are removed. On the reference run that was
            83 198 of 286 056 on the first pass alone — the back of everything
            the capture never walked round.
          </p>
        </div>
        <Switch
          checked={settings.cull_unseen}
          onCheckedChange={(v) => update('cull_unseen', v)}
        />
      </div>
    </div>
  );
};

export default MeshSettings;
