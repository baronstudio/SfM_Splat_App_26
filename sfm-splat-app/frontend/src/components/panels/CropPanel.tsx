import React from 'react';
import {
  AlertTriangle, Box, Circle, Eye, EyeOff, Move3d, Plus, RotateCw,
  Scaling, Scissors, Trash2,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  MAX_CROP_VOLUMES, MIN_HALF,
  type CropGizmoMode, type CropKind, type CropMode, type CropVolume,
} from '@/components/viewer/cropVolumes';
import type { CropState } from '@/types';

/**
 * The crop tool's controls (CLAUDE.md §7.6b).
 *
 * The viewer is where the volume is placed; this is the list of what has been
 * placed, and the one button that turns a preview into a file. The split
 * matters: everything on this panel except **Apply** is free and instant,
 * because the live cut is a shader test on a decimated preview. Apply is the
 * only thing that reads the 178 MB PLY, and it is the only thing steps 5 and 6
 * can see.
 *
 * Two states the panel exists to make impossible to miss:
 *
 * * **stale** — a crop is on disk and the volumes have been dragged since. The
 *   file is not wrong, it is *old*, and steps 5 and 6 would read it without
 *   knowing. Nothing re-runs on its own; the panel says so and Apply is one
 *   click.
 * * **no live preview** — the splat library's shader was not the one
 *   `cropShader.ts` knows how to patch, so the volumes are drawn but nothing is
 *   hidden. Better to say it than to show an uncropped scene next to a crop
 *   panel and let it be read as "the crop selects everything".
 */

interface CropPanelProps {
  volumes: CropVolume[];
  selectedId: string | null;
  gizmoMode: CropGizmoMode;
  showVolumes: boolean;
  livePreview: boolean;
  liveSupported: boolean;
  state: CropState | null;
  /** Volumes edited but not applied — the disk file is behind the panel. */
  dirty: boolean;
  running: boolean;
  error: string | null;
  onSelect(id: string | null): void;
  onAdd(kind: CropKind, mode: CropMode): void;
  onUpdate(volume: CropVolume): void;
  onRemove(id: string): void;
  onClear(): void;
  onGizmoMode(mode: CropGizmoMode): void;
  onShowVolumes(show: boolean): void;
  onLivePreview(live: boolean): void;
  onApply(): void;
}

const MODES: { value: CropGizmoMode; label: string; icon: typeof Move3d }[] = [
  { value: 'translate', label: 'Move', icon: Move3d },
  { value: 'rotate', label: 'Rotate', icon: RotateCw },
  { value: 'scale', label: 'Scale', icon: Scaling },
];

function formatCount(value: number | null | undefined): string {
  return typeof value === 'number' ? value.toLocaleString() : '—';
}

/** One numeric field of a volume's transform. */
const Num: React.FC<{
  value: number;
  onChange(v: number): void;
  step: number;
  min?: number;
}> = ({ value, onChange, step, min }) => (
  <input
    type="number"
    value={Number(value.toFixed(4))}
    step={step}
    min={min}
    onChange={(e) => {
      const next = Number(e.target.value);
      if (Number.isFinite(next)) onChange(min !== undefined ? Math.max(next, min) : next);
    }}
    className="w-full bg-slate-900 border border-slate-700 rounded px-1.5 py-1
               text-xs font-mono text-slate-200 focus:border-cyan-600 focus:outline-none"
  />
);

export const CropPanel: React.FC<CropPanelProps> = ({
  volumes, selectedId, gizmoMode, showVolumes, livePreview, liveSupported,
  state, dirty, running, error,
  onSelect, onAdd, onUpdate, onRemove, onClear,
  onGizmoMode, onShowVolumes, onLivePreview, onApply,
}) => {
  const selected = volumes.find((v) => v.id === selectedId) ?? null;
  const full = volumes.length >= MAX_CROP_VOLUMES;
  const applied = state?.applied ?? null;

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/60 p-3 space-y-3">
      {/* Add + view toggles */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-slate-300 mr-1">Crop volumes</span>

        <Button
          variant="ghost" size="sm" disabled={full}
          onClick={() => onAdd('box', 'keep')}
          className="gap-1 text-xs text-slate-300 hover:text-cyan-300"
          title="A box that keeps what is inside it. With no keep volume the scene starts whole"
        >
          <Plus className="w-3 h-3" /><Box className="w-3.5 h-3.5" /> Keep box
        </Button>
        <Button
          variant="ghost" size="sm" disabled={full}
          onClick={() => onAdd('sphere', 'delete')}
          className="gap-1 text-xs text-slate-300 hover:text-red-300"
          title="A sphere that removes what is inside it. Delete always wins over keep"
        >
          <Plus className="w-3 h-3" /><Circle className="w-3.5 h-3.5" /> Delete sphere
        </Button>

        <div className="flex-1" />

        <Button
          variant="ghost" size="sm"
          onClick={() => onLivePreview(!livePreview)}
          disabled={!liveSupported}
          className={`gap-1 text-xs ${livePreview && liveSupported ? 'text-cyan-400' : 'text-slate-500'}`}
          title="Hide the gaussians the crop excludes, live. Off shows the whole splat under the volumes"
        >
          {livePreview ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5" />}
          Live cut
        </Button>
        <Button
          variant="ghost" size="sm"
          onClick={() => onShowVolumes(!showVolumes)}
          className={`gap-1 text-xs ${showVolumes ? 'text-cyan-400' : 'text-slate-500'}`}
          title="Draw the volumes and the gizmo"
        >
          <Box className="w-3.5 h-3.5" /> Volumes
        </Button>
      </div>

      {full && (
        <p className="text-xs text-amber-400">
          {MAX_CROP_VOLUMES} volumes is the limit — the preview shader carries a
          fixed-length array, and the backend refuses more for the same reason.
        </p>
      )}

      {volumes.length === 0 && (
        <p className="text-xs text-slate-500">
          No volumes. Add a <span className="text-cyan-400">keep</span> box to
          throw away everything outside it, or a{' '}
          <span className="text-red-400">delete</span> sphere to carve a floater
          out of a scene that is otherwise fine. Click a volume in the viewer to
          select it; drag its handles to place it.
        </p>
      )}

      {/* The stack */}
      {volumes.length > 0 && (
        <div className="space-y-1">
          {volumes.map((volume, index) => {
            const active = volume.id === selectedId;
            const remove = volume.mode === 'delete';
            return (
              <div
                key={volume.id}
                onClick={() => onSelect(active ? null : volume.id)}
                className={`flex items-center gap-2 rounded px-2 py-1.5 cursor-pointer
                            border transition-colors ${
                  active
                    ? 'border-cyan-600 bg-slate-800'
                    : 'border-transparent bg-slate-800/40 hover:bg-slate-800'
                }`}
              >
                {volume.kind === 'sphere'
                  ? <Circle className={`w-3.5 h-3.5 ${remove ? 'text-red-400' : 'text-cyan-400'}`} />
                  : <Box className={`w-3.5 h-3.5 ${remove ? 'text-red-400' : 'text-cyan-400'}`} />}
                <span className="text-xs text-slate-300">
                  {index + 1}. {volume.kind}
                </span>

                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onUpdate({ ...volume, mode: remove ? 'keep' : 'delete' });
                  }}
                  className={`text-xs px-1.5 py-0.5 rounded border ${
                    remove
                      ? 'border-red-700 text-red-300 bg-red-950/40'
                      : 'border-cyan-700 text-cyan-300 bg-cyan-950/30'
                  }`}
                  title="Keep what is inside, or delete it — this is the invert"
                >
                  {remove ? 'delete inside' : 'keep inside'}
                </button>

                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onUpdate({
                      ...volume,
                      kind: volume.kind === 'box' ? 'sphere' : 'box',
                    });
                  }}
                  className="text-xs text-slate-500 hover:text-slate-300"
                  title="Box or sphere"
                >
                  → {volume.kind === 'box' ? 'sphere' : 'box'}
                </button>

                <div className="flex-1" />
                <button
                  onClick={(e) => { e.stopPropagation(); onRemove(volume.id); }}
                  className="text-slate-500 hover:text-red-400"
                  title="Remove this volume"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            );
          })}
        </div>
      )}

      {/* The selected volume: gizmo mode + the numbers behind the handles */}
      {selected && (
        <div className="space-y-2 rounded border border-slate-700 bg-slate-950/50 p-2">
          <div className="flex items-center gap-1">
            {MODES.map(({ value, label, icon: Icon }) => (
              <button
                key={value}
                onClick={() => onGizmoMode(value)}
                className={`flex items-center gap-1 px-2 py-1 text-xs rounded transition-colors ${
                  gizmoMode === value
                    ? 'bg-cyan-600 text-white'
                    : 'text-slate-400 hover:text-slate-100'
                }`}
              >
                <Icon className="w-3 h-3" /> {label}
              </button>
            ))}
            <div className="flex-1" />
            <span className="text-xs text-slate-600">dataset frame</span>
          </div>

          <div className="grid grid-cols-[3rem_1fr_1fr_1fr] items-center gap-1">
            <span className="text-xs text-slate-500">Centre</span>
            {([0, 1, 2] as const).map((axis) => (
              <Num
                key={`c${axis}`}
                value={selected.center[axis]}
                step={0.05}
                onChange={(v) => {
                  const center = [...selected.center] as [number, number, number];
                  center[axis] = v;
                  onUpdate({ ...selected, center });
                }}
              />
            ))}
            <span className="text-xs text-slate-500">Size</span>
            {([0, 1, 2] as const).map((axis) => (
              <Num
                key={`h${axis}`}
                value={selected.half[axis] * 2}
                step={0.05}
                min={MIN_HALF * 2}
                onChange={(v) => {
                  const half = [...selected.half] as [number, number, number];
                  half[axis] = Math.max(v / 2, MIN_HALF);
                  onUpdate({ ...selected, half });
                }}
              />
            ))}
          </div>
        </div>
      )}

      {/* Apply, and what is on disk */}
      <div className="flex flex-wrap items-center gap-2 pt-1 border-t border-slate-800">
        <Button
          size="sm"
          onClick={onApply}
          disabled={running || !state?.source.available}
          className="gap-1 text-xs bg-cyan-600 hover:bg-cyan-500"
          title="Run the cut over the full splat.ply and write train/crop/splat.ply"
        >
          <Scissors className="w-3.5 h-3.5" />
          {running ? 'Cutting…'
            : volumes.length === 0 ? 'Clear the crop'
            : 'Apply crop'}
        </Button>

        {volumes.length > 0 && (
          <Button
            variant="ghost" size="sm" onClick={onClear} disabled={running}
            className="text-xs text-slate-400 hover:text-slate-100"
          >
            Remove all
          </Button>
        )}

        <div className="flex-1" />

        <span className="text-xs text-slate-500">
          {applied
            ? `${formatCount(applied.kept)} of ${formatCount(applied.source_count)} kept`
            : `${formatCount(state?.source.count)} gaussians, uncropped`}
        </span>
      </div>

      {!liveSupported && volumes.length > 0 && (
        <p className="flex items-start gap-2 text-xs text-amber-400">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-px" />
          The live cut could not be installed in the splat shader, so the viewer
          is showing every gaussian under the volumes. The volumes are still
          exact — Apply cuts the file the same way either way.
        </p>
      )}

      {dirty && state?.cropped && (
        <p className="flex items-start gap-2 text-xs text-amber-400">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-px" />
          <span>
            <span className="font-medium">train/crop/splat.ply is behind these
            volumes.</span> Steps 5 and 6 read the file, not the panel — apply
            the crop before meshing, or remove all volumes to go back to the
            full splat.
          </span>
        </p>
      )}

      {!dirty && state?.cropped && applied && (
        <p className="text-xs text-slate-500">
          Steps 5 and 6 read <span className="font-mono">{applied.output}</span> —{' '}
          {formatCount(applied.removed)} gaussians removed
          {' '}({(100 * applied.removed / Math.max(applied.source_count, 1)).toFixed(1)}%)
          in {applied.seconds}s. The trained splat is untouched.
        </p>
      )}

      {state && !state.valid && (
        <p className="flex items-start gap-2 text-xs text-red-400">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-px" />
          The stored volumes do not parse — the crop pass will refuse them and
          name the one at fault.
        </p>
      )}

      {error && (
        <p className="flex items-start gap-2 text-xs text-red-400 bg-red-950/30
                      border border-red-800 rounded px-2 py-1.5">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-px" />
          {error}
        </p>
      )}
    </div>
  );
};

export default CropPanel;
