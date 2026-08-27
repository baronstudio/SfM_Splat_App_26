import React from 'react';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Slider } from '@/components/ui/slider';
import { Separator } from '@/components/ui/separator';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Info } from 'lucide-react';
import type { CapturePreset, CurateDefaults } from '@/types';

interface CurateSettingsProps {
  settings: CurateDefaults;
  /** The preset the project extracts with — it carries the overlap band (§6.2). */
  preset?: CapturePreset;
  onChange: (s: CurateDefaults) => void;
}

const Row: React.FC<{ label: string; value?: string; hint?: string; children: React.ReactNode }> = ({
  label, value, hint, children,
}) => (
  <div className="space-y-2">
    <div className="flex items-center justify-between">
      <Label>{label}</Label>
      {value && <span className="text-xs text-cyan-400 font-mono">{value}</span>}
    </div>
    {children}
    {hint && <p className="text-xs text-slate-500">{hint}</p>}
  </div>
);

const CurateSettings: React.FC<CurateSettingsProps> = ({ settings, preset, onChange }) => {
  const update = <K extends keyof CurateDefaults>(key: K, value: CurateDefaults[K]) => {
    onChange({ ...settings, [key]: value });
  };

  const bandMin = settings.overlap_from_preset && preset
    ? preset.overlap_min_step_pct : settings.overlap_min_step_pct;
  const bandMax = settings.overlap_from_preset && preset
    ? preset.overlap_band_max_pct : settings.overlap_band_max_pct;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <Label>Curation</Label>
          <p className="text-xs text-slate-500">Blur rejection, scene cuts and the overlap gate.</p>
        </div>
        <Switch
          checked={settings.enabled}
          onCheckedChange={(v) => update('enabled', v)}
        />
      </div>

      {settings.enabled && (
        <>
          <Separator className="bg-slate-700/50" />

          {/* ── Scenes ── */}
          <Row
            label="Cut detection"
            hint="Each cut splits the footage into a sequence. Sequences reset the overlap gate and should be imported into RealityScan as separate image groups."
          >
            <Select
              value={settings.scene_detector}
              onValueChange={(v) => update('scene_detector', v as CurateDefaults['scene_detector'])}
            >
              <SelectTrigger className="bg-slate-950 border-slate-700 text-slate-100">
                <SelectValue />
              </SelectTrigger>
              <SelectContent className="bg-slate-800 border-slate-700 text-slate-100">
                <SelectItem value="adaptive">Adaptive (recommended)</SelectItem>
                <SelectItem value="content">Content</SelectItem>
                <SelectItem value="off">Off — one single sequence</SelectItem>
              </SelectContent>
            </Select>
          </Row>

          {settings.scene_detector !== 'off' && (
            <Row
              label="Detected from"
              hint="Auto reads the scene scores FFmpeg captured while it was extracting, so curation never decodes the source a second time — and falls back to PySceneDetect on its own when they are missing or the extraction predates them. PySceneDetect is the reference detector; it costs a full decode of the video."
            >
              <Select
                value={settings.cut_source}
                onValueChange={(v) => update('cut_source', v as CurateDefaults['cut_source'])}
              >
                <SelectTrigger className="bg-slate-950 border-slate-700 text-slate-100">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="bg-slate-800 border-slate-700 text-slate-100">
                  <SelectItem value="auto">Auto — from the extraction (fast)</SelectItem>
                  <SelectItem value="video">PySceneDetect on the source video</SelectItem>
                  <SelectItem value="frames">The extracted frames only</SelectItem>
                </SelectContent>
              </Select>
            </Row>
          )}

          <Row
            label="Minimum sequence length"
            value={`${settings.min_scene_len} frames`}
            hint="Shorter runs are merged into the neighbouring sequence — a handful of images cannot align on their own anyway."
          >
            <Slider
              min={5} max={60} step={1}
              value={[settings.min_scene_len]}
              onValueChange={([v]) => update('min_scene_len', v)}
            />
          </Row>

          <Separator className="bg-slate-700/50" />

          {/* ── Sharpness ── */}
          <Row
            label="Blur sensitivity"
            value={`${settings.sharpness_sensitivity}%`}
            hint="Share of the local sharpness median a frame must reach to survive. 0 rejects nothing, 100 rejects everything below the median. The threshold is relative on purpose — an absolute one does not generalise across content."
          >
            <Slider
              min={0} max={100} step={5}
              value={[settings.sharpness_sensitivity]}
              onValueChange={([v]) => update('sharpness_sensitivity', v)}
            />
          </Row>

          <Row
            label="Median window"
            value={`${settings.sharpness_window} frames`}
            hint="Width of the rolling window the median is taken over. The window never straddles a cut."
          >
            <Slider
              min={5} max={51} step={2}
              value={[settings.sharpness_window]}
              onValueChange={([v]) => update('sharpness_window', v)}
            />
          </Row>

          <Separator className="bg-slate-700/50" />

          {/* ── Overlap ── */}
          <div className="flex items-center justify-between">
            <div>
              <Label>Overlap band from preset</Label>
              <p className="text-xs text-slate-500">
                {preset
                  ? `'${preset.label}' → ${preset.overlap_min_step_pct}–${preset.overlap_band_max_pct}%`
                  : 'No preset resolved.'}
              </p>
            </div>
            <Switch
              checked={settings.overlap_from_preset}
              onCheckedChange={(v) => update('overlap_from_preset', v)}
            />
          </div>

          <Row
            label="Minimum step"
            value={`${bandMin}%`}
            hint="Below this displacement from the last kept frame, the frame adds nothing → rejected as redundant."
          >
            <Slider
              min={0.5} max={8} step={0.5}
              disabled={settings.overlap_from_preset}
              value={[bandMin]}
              onValueChange={([v]) => update('overlap_min_step_pct', v)}
            />
          </Row>

          <Row
            label="Maximum step"
            value={`${bandMax}%`}
            hint="Above this the frame is still kept, but flagged as a gap — it is where alignment is most likely to break."
          >
            <Slider
              min={4} max={30} step={0.5}
              disabled={settings.overlap_from_preset}
              value={[bandMax]}
              onValueChange={([v]) => update('overlap_band_max_pct', v)}
            />
          </Row>

          <p className="flex gap-1.5 text-xs text-slate-500">
            <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            Changing any of these only needs a re-analysis — the extracted frames are never touched.
          </p>
        </>
      )}
    </div>
  );
};

export default CurateSettings;
