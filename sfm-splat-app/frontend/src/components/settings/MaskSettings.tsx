import React from 'react';
import { AlertTriangle, Info, ShieldAlert } from 'lucide-react';
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
import type { SamDefaults, SamLicence } from '@/types';

/**
 * Masking — `spirula sam` (CLAUDE.md §7.4). Shared by step 3's panel and the
 * setup panel's Masks section, so the refusal below exists once rather than
 * twice.
 *
 * Two routes, one setting with a mode, because their costs differ by
 * everything. **Lens border** is `sam mask`: no model, no download, no licence
 * question, and safe to run speculatively — on 238 rectilinear frames it
 * answered `no border found` and exited 0 having written nothing. **Track
 * objects** is `sam track`: it needs a SAM checkpoint the user downloaded by
 * hand, and that checkpoint's licence is a row in §10.
 *
 * The licence is asked as *which one*, never as a tick box, and it is the one
 * thing this panel enforces. SAM 2.1 is Apache-2.0 and SAM 3 is Meta's own
 * non-standard licence: a single "I agree" covering both would be answering the
 * harder question by accident, so they are accepted separately.
 */

const LICENCES: { value: Exclude<SamLicence, ''>; label: string; note: string }[] = [
  {
    value: 'sam2.1',
    label: 'SAM 2.1 — Apache-2.0',
    note: 'A permissive, standard open-source licence.',
  },
  {
    value: 'sam3',
    label: "SAM 3 — Meta's own licence",
    note: "NOT Apache-2.0. A bespoke corporate licence with its own terms — read it in full before using this checkpoint.",
  },
];

/**
 * The refusal message for a mask run that cannot work, or null.
 * Mirrors `step_sam.check_settings`, which is the half a run started from
 * anywhere else still hits.
 */
export function maskRefusal(settings: SamDefaults): string | null {
  if (settings.mode === 'off') {
    return 'Masking is off. Pick a mode before running it.';
  }
  if (settings.mode === 'track') {
    if (!settings.model.trim()) {
      return 'Give the path of a SAM checkpoint. They are never bundled with this app — download one by hand.';
    }
    if (!settings.model_licence) {
      return "Say which licence this checkpoint is under. SAM 2.1 and SAM 3 are accepted separately, because they are not the same terms.";
    }
    if (!settings.text.trim() && !settings.neg_text.trim()) {
      return 'Name what to track — "person; car" — or use the lens-border mode, which needs no prompt and no model.';
    }
  }
  return null;
}

interface MaskSettingsProps {
  settings: SamDefaults;
  /** Frames on disk, for the copy that says what the run will read. */
  frameCount?: number;
  onChange: (s: SamDefaults) => void;
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

const MaskSettings: React.FC<MaskSettingsProps> = ({
  settings, frameCount = 0, onChange,
}) => {
  const update = <K extends keyof SamDefaults>(key: K, value: SamDefaults[K]) => {
    onChange({ ...settings, [key]: value });
  };

  const refusal = maskRefusal(settings);

  return (
    <div className="space-y-6">
      <div>
        <Label>Mode</Label>
        <p className="text-xs text-slate-500">
          Masks are written to <span className="font-mono">masks/</span>, one
          greyscale PNG per frame. Step 3 adopts them with no flag at all; step 4
          is pointed at them explicitly.
        </p>
      </div>

      <Row label="What to mask">
        <Select
          value={settings.mode}
          onValueChange={(v) => update('mode', v as SamDefaults['mode'])}
        >
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="off">Off — no masking</SelectItem>
            <SelectItem value="shape">
              Lens border / fixed shape — no model, no download
            </SelectItem>
            <SelectItem value="track">
              Track objects — needs a SAM checkpoint
            </SelectItem>
          </SelectContent>
        </Select>
      </Row>

      {settings.mode === 'shape' && (
        <>
          <p className="flex gap-2 text-xs text-slate-400 bg-slate-900/50 border border-slate-700 rounded px-3 py-2">
            <Info className="w-4 h-4 mt-px shrink-0 text-cyan-500" />
            <span>
              Masks the part of every frame that is never scene — a fisheye
              border, a watermark, the rig in shot. It is in the same place in
              every frame, so it is a shape rather than an object, and it costs
              no model and no download. Safe to run speculatively: with no shape
              named and no border to find it writes nothing and exits cleanly.
            </span>
          </p>

          <Row
            label="Shape"
            hint={'Empty looks for the border itself. Otherwise: "ellipse cx,cy,rx,ry" or "rect x0,y0,x1,y1" in 0..1 of the frame, ";" between several, and a leading "-" cuts one back out. Example: ellipse 0.5,0.5,0.49,0.49; -rect 0,0.93,1,1'}
          >
            <Input
              value={settings.shape_spec}
              placeholder="look for the border automatically"
              onChange={(e) => update('shape_spec', e.target.value)}
              className="font-mono text-xs"
            />
          </Row>

          <div className="grid grid-cols-3 gap-3">
            <Row
              label="Shrink"
              value={settings.shrink.toFixed(3)}
              hint="Fraction of its radius the found boundary is pulled inwards."
            >
              <Input
                type="number" step="0.005" min={0} max={0.5}
                value={settings.shrink}
                onChange={(e) => update('shrink', Number(e.target.value))}
              />
            </Row>
            <Row label="Samples" hint="Frames read per camera when looking.">
              <Input
                type="number" min={1}
                value={settings.samples}
                onChange={(e) => update('samples', Number(e.target.value))}
              />
            </Row>
            <Row label="Dark" hint="At or below this, a pixel counts as black.">
              <Input
                type="number" min={0} max={255}
                value={settings.dark}
                onChange={(e) => update('dark', Number(e.target.value))}
              />
            </Row>
          </div>
          <p className="text-xs text-slate-500">
            The outermost pixels of a lens circle are dim and smeared, and worth
            losing — which is what Shrink is for.
          </p>
        </>
      )}

      {settings.mode === 'track' && (
        <>
          <p className="flex gap-2 text-xs text-amber-200 bg-amber-950/30 border border-amber-800 rounded px-3 py-2">
            <ShieldAlert className="w-4 h-4 mt-px shrink-0" />
            <span>
              The SAM checkpoints are <strong>never bundled</strong> with this
              app and nothing here downloads one. Fetch it by hand, then give its
              path below and say which licence it is under.
            </span>
          </p>

          <Row
            label="Checkpoint"
            hint="The .pt / .onnx file you downloaded. `sam track --model` takes a file, not a name to fetch."
          >
            <Input
              value={settings.model}
              placeholder="C:/models/sam2.1_hiera_large.pt"
              onChange={(e) => update('model', e.target.value)}
              className="font-mono text-xs"
            />
          </Row>

          <div className="space-y-2">
            <Label>Licence of that checkpoint</Label>
            <div className="space-y-2">
              {LICENCES.map((l) => {
                const on = settings.model_licence === l.value;
                return (
                  <button
                    key={l.value}
                    type="button"
                    onClick={() => update('model_licence', on ? '' : l.value)}
                    className={`w-full rounded-md border px-3 py-2 text-left text-sm transition-colors ${
                      on
                        ? 'border-cyan-600 bg-cyan-950/40 text-slate-100'
                        : 'border-slate-700 bg-slate-900/40 text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    <span className="font-medium">{l.label}</span>
                    <span className="block text-[11px] text-slate-500">{l.note}</span>
                  </button>
                );
              })}
            </div>
            <p className="text-xs text-slate-500">
              Asked as <em>which one</em> rather than as a tick box: the two are
              not the same terms, so accepting one is not accepting the other.
            </p>
          </div>

          <Separator className="bg-slate-800" />

          <Row
            label="Track these"
            hint='Semicolon-separated for several concepts: "person; car; bicycle".'
          >
            <Input
              value={settings.text}
              placeholder="person; car"
              onChange={(e) => update('text', e.target.value)}
            />
          </Row>

          <Row
            label="Keep these even where the prompt matches"
            hint="Optional. Concepts to spare from the mask."
          >
            <Input
              value={settings.neg_text}
              placeholder="(none)"
              onChange={(e) => update('neg_text', e.target.value)}
            />
          </Row>

          <div className="grid grid-cols-2 gap-3">
            <Row label="Detect every" hint="Frames between detector runs; the memory bank carries tracks in between.">
              <Input
                type="number" min={1}
                value={settings.detect_every}
                onChange={(e) => update('detect_every', Number(e.target.value))}
              />
            </Row>
            <Row label="Longest side" hint="Inputs are downscaled to fit. 0 turns it off.">
              <Input
                type="number" min={0}
                value={settings.max_size}
                onChange={(e) => update('max_size', Number(e.target.value))}
              />
            </Row>
            <Row label="Score threshold" value={settings.threshold.toFixed(2)}>
              <Input
                type="number" step="0.05" min={0} max={1}
                value={settings.threshold}
                onChange={(e) => update('threshold', Number(e.target.value))}
              />
            </Row>
            <Row label="NMS IoU" value={settings.nms.toFixed(2)}>
              <Input
                type="number" step="0.05" min={0} max={1}
                value={settings.nms}
                onChange={(e) => update('nms', Number(e.target.value))}
              />
            </Row>
          </div>

          <div className="flex items-center justify-between rounded-md border border-slate-700 bg-slate-900/40 px-3 py-2">
            <div className="pr-4">
              <Label>The prompt names the subject, not the distractors</Label>
              <p className="text-xs text-slate-500">
                Off — the default — masks out what you named, which is what a
                reconstruction wants from "mask out the people". On keeps what
                you named and masks everything else.
              </p>
            </div>
            <Switch
              checked={settings.keep_prompted}
              onCheckedChange={(v) => update('keep_prompted', v)}
            />
          </div>
        </>
      )}

      {settings.mode !== 'off' && (
        <>
          <Separator className="bg-slate-800" />
          <div className="flex items-center justify-between rounded-md border border-slate-700 bg-slate-900/40 px-3 py-2">
            <div className="pr-4">
              <Label>Replace the masks already there</Label>
              <p className="text-xs text-slate-500">
                Off — the default — <em>intersects</em> this run with the masks
                already in the folder, which is how a lens border stacks on top
                of a tracked object instead of undoing it.
              </p>
            </div>
            <Switch
              checked={settings.replace}
              onCheckedChange={(v) => update('replace', v)}
            />
          </div>
        </>
      )}

      {refusal && (
        <p className="flex gap-2 text-xs text-red-300 bg-red-950/30 border border-red-800 rounded px-3 py-2">
          <AlertTriangle className="w-4 h-4 mt-px shrink-0" />
          <span>{refusal}</span>
        </p>
      )}

      {settings.mode !== 'off' && frameCount > 0 && !refusal && (
        <p className="text-xs text-slate-500">
          Will run over <span className="text-cyan-400 font-mono">{frameCount}</span>{' '}
          frames and write one greyscale PNG each, named after the frame.
        </p>
      )}
    </div>
  );
};

export default MaskSettings;
