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
import type { GeometryDefaults } from '@/types';

/**
 * Geometry supervision — `spirula geometry` (CLAUDE.md §7.5). Shared by step 4's
 * panel and the setup panel's Geometry section.
 *
 * Per-image depth and normal maps feeding the trainer's geometry terms. Not a
 * step: it writes `sfm/normals/` and `sfm/depths/` *inside* the dataset, which
 * `train` finds by name through `--data` with no flag at all.
 *
 * Depth is off in the tool's own default and stays off here: the normals are
 * what a reconstruction usually wants, and depth doubles both the time on disk
 * and the reading a training run does. `--ray-depth auto` and `--split auto` are
 * the coherent pair with the trainer's own unset defaults, so both stay on auto
 * unless there is a reason.
 */

interface GeometrySettingsProps {
  settings: GeometryDefaults;
  onChange: (s: GeometryDefaults) => void;
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

const GeometrySettings: React.FC<GeometrySettingsProps> = ({ settings, onChange }) => {
  const update = <K extends keyof GeometryDefaults>(
    key: K, value: GeometryDefaults[K],
  ) => onChange({ ...settings, [key]: value });

  return (
    <div className="space-y-6">
      <p className="flex gap-2 text-xs text-slate-400 bg-slate-900/50 border border-slate-700 rounded px-3 py-2">
        <Info className="w-4 h-4 mt-px shrink-0 text-cyan-500" />
        <span>
          Estimates a surface normal — and optionally a depth — for every image
          from that image alone, into{' '}
          <span className="font-mono">sfm/normals/</span> and{' '}
          <span className="font-mono">sfm/depths/</span>. Nothing rewrites the
          reconstruction, and step 4 reads them through{' '}
          <span className="font-mono">--data</span> with no flag. A step 3 re-run
          deletes them with the rest of <span className="font-mono">sfm/</span>.
        </span>
      </p>

      <Row
        label="Checkpoint"
        hint="Empty lets the build fetch its own mid-run on first use — moge2-vitb-normal.onnx, 419.4 MB, over a curl child. Install it from Setup → Checkpoints instead and this holds its path, which never opens the network. A known id is also accepted and is fetched. Larger is slower and only somewhat better."
      >
        <Input
          value={settings.model}
          placeholder="the build's own default (moge2-vitb-normal)"
          onChange={(e) => update('model', e.target.value)}
          className="font-mono text-xs"
        />
      </Row>

      <Row
        label="Longest side"
        value={`${settings.max_size} px`}
        hint="The size the network runs at and the size of the maps written. 1064 is what the model's own pipeline uses. A split sizes its faces off the full frame and meets this only as a ceiling."
      >
        <Input
          type="number" min={128} step={8}
          value={settings.max_size}
          onChange={(e) => update('max_size', Number(e.target.value))}
        />
      </Row>

      <Row
        label="Normal map format"
        hint="jpg is about a fifth the size, and the loss lands on the normal's direction, which the geometry term reads directly."
      >
        <Choice
          value={settings.normal_format}
          onChange={(v) => update('normal_format', v)}
          options={[
            { value: 'jpg', label: 'JPEG — about a fifth the size' },
            { value: 'png', label: 'PNG — lossless' },
          ]}
        />
      </Row>

      {settings.normal_format === 'jpg' && (
        <Row label="JPEG quality" value={String(settings.jpeg_quality)}>
          <Input
            type="number" min={1} max={100}
            value={settings.jpeg_quality}
            onChange={(e) => update('jpeg_quality', Number(e.target.value))}
          />
        </Row>
      )}

      <p className="text-xs text-amber-300/80">
        Switching format writes the new maps <em>beside</em> the old rather than
        over them — the run says so and names the count when it happens.
      </p>

      <Separator className="bg-slate-800" />

      <div className="flex items-center justify-between rounded-md border border-slate-700 bg-slate-900/40 px-3 py-2">
        <div className="pr-4">
          <Label>Also write depth maps</Label>
          <p className="text-xs text-slate-500">
            Off in the tool's own default and off here: the normals are what a
            reconstruction usually wants, and depth doubles both the time on disk
            and the reading a training run does.
          </p>
        </div>
        <Switch
          checked={settings.depth}
          onCheckedChange={(v) => update('depth', v)}
        />
      </div>

      {settings.depth && (
        <Row
          label="Depth units"
          hint="relative fills the 16 bits with the scene and is what the depth term wants — it correlates log depths and ignores any scale. mm is readable but flattens everything past 65.5 m."
        >
          <Choice
            value={settings.depth_units}
            onChange={(v) => update('depth_units', v)}
            options={[
              { value: 'relative', label: 'Relative — what the depth term wants' },
              { value: 'mm', label: 'Millimetres — readable, flat past 65.5 m' },
            ]}
          />
        </Row>
      )}

      <div className="grid grid-cols-2 gap-3">
        <Row
          label="Ray depth"
          hint="auto picks ray depth exactly when the frame was split into pinhole faces — the same call the trainer makes when left unset."
        >
          <Choice
            value={settings.ray_depth}
            onChange={(v) => update('ray_depth', v)}
            options={[
              { value: 'auto', label: 'Auto' },
              { value: 'yes', label: 'Along the ray' },
              { value: 'no', label: 'Z coordinate' },
            ]}
          />
        </Row>
        <Row
          label="Split wide frames"
          hint="auto splits a panorama always and a fisheye when one pinhole would keep less than three quarters of the frame."
        >
          <Choice
            value={settings.split}
            onChange={(v) => update('split', v)}
            options={[
              { value: 'auto', label: 'Auto' },
              { value: 'yes', label: 'Always split' },
              { value: 'no', label: 'Never split' },
            ]}
          />
        </Row>
      </div>

      <div className="flex items-center justify-between rounded-md border border-slate-700 bg-slate-900/40 px-3 py-2">
        <div className="pr-4">
          <Label>Recompute maps already on disk</Label>
          <p className="text-xs text-slate-500">
            Off — the default — continues where the last run stopped, which is
            what makes an aborted pass cheap to resume.
          </p>
        </div>
        <Switch
          checked={settings.overwrite}
          onCheckedChange={(v) => update('overwrite', v)}
        />
      </div>
    </div>
  );
};

export default GeometrySettings;
