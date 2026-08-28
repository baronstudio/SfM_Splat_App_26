import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle, CheckCircle, Info, RefreshCw, Sliders, Split,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { usePipelineStore } from '@/store/pipelineStore';
import { usePipeline } from '@/hooks/usePipeline';
import { useDefaults } from '@/hooks/useDefaults';
import { useCuration } from '@/hooks/useCuration';
import { useProjectSettings } from '@/hooks/useProjectSettings';
import { ProgressBar } from '@/components/panels/ProgressBar';
import SceneViewer from '@/components/viewer/SceneViewer';
import SfmSettings from '@/components/settings/SfmSettings';
import SaveState from '@/components/settings/SaveState';
import client from '@/api/client';
import type { SfmDefaults, SfmResult } from '@/types';

/** What `masks/` holds, off the folder rather than off a log line. */
interface MaskState {
  masks: number;
  frames: number;
  matched: number;
  state: 'none' | 'ready' | 'unmatched';
  note: string;
}

const Stat: React.FC<{ label: string; value: string; tone?: string; hint?: string }> = ({
  label, value, tone = 'text-slate-100', hint,
}) => (
  <div className="flex flex-col" title={hint}>
    <span className={`text-lg font-semibold ${tone}`}>{value}</span>
    <span className="text-[11px] text-slate-500 uppercase tracking-wide">{label}</span>
  </div>
);

/**
 * How the last reconstruction went, persisted rather than scrolled past.
 *
 * `sfm auto` grades its own result in the exit code — 0 sound, 2 nothing
 * reconstructed, 3 partial — and exit 3 warns without failing the pipeline
 * (CLAUDE.md §7.1). So "the step is green" and "the reconstruction is good" are
 * two different statements here, and this panel is where the second one lives.
 */
const SfmReport: React.FC<{ result: SfmResult }> = ({ result }) => {
  const partial = result.exit_code === 3;
  const fragmented = result.sparse_models > 1;
  const coverage = result.registered !== undefined && result.total
    ? result.registered / result.total
    : null;

  return (
    <div className="space-y-3">
      <div className="rounded-lg bg-slate-800 border border-slate-700 px-4 py-3">
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-4">
          <Stat
            label="registered"
            value={result.registered !== undefined
              ? `${result.registered}/${result.total ?? result.images}` : '—'}
            tone={coverage === null ? 'text-slate-400'
              : coverage >= 0.99 ? 'text-green-400'
                : coverage >= 0.5 ? 'text-amber-400' : 'text-red-400'}
            hint="Images the mapper placed in the model."
          />
          <Stat
            label="reprojection"
            value={result.reprojection_mean_px !== undefined
              ? `${result.reprojection_mean_px.toFixed(2)} px` : '—'}
            tone={(result.reprojection_mean_px ?? 0) > 2 ? 'text-amber-400' : 'text-green-400'}
            hint={result.reprojection_median_px !== undefined
              ? `median ${result.reprojection_median_px.toFixed(2)} px over ${result.observations ?? '?'} observations`
              : 'Mean reprojection error.'}
          />
          <Stat
            label="points"
            value={result.points !== undefined ? result.points.toLocaleString() : '—'}
          />
          <Stat
            label="camera groups"
            value={result.camera_groups !== undefined ? String(result.camera_groups) : '—'}
            hint="Intrinsics, not components — folder grouping splits on image resolution first."
          />
          <Stat
            label="models"
            value={String(result.sparse_models)}
            tone={fragmented ? 'text-amber-400' : 'text-slate-100'}
            hint="sparse/N directories. More than one means the capture is not one connected view graph."
          />
        </div>
        <p className="text-xs text-slate-500 mt-2">
          exit {result.exit_code} — {result.exit_meaning}
          {result.elapsed_s !== undefined && ` · ${result.elapsed_s.toFixed(1)} s`}
          {` · quality ${result.quality} · ${result.data_type}`}
          {` · ${result.masks_used ? `${result.mask_count} masks` : 'no masks'}`}
          {` · spirula ${result.spirula_version}`}
        </p>
      </div>

      {partial && (
        <p className="flex gap-2 text-sm text-amber-300 bg-amber-950/20 border border-amber-800/60 rounded px-3 py-2">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          <span>
            Partial reconstruction (exit 3): under half the images registered, or
            over 2 px mean reprojection. The pipeline is not blocked — this is
            often still the model you want to train on. If step 4 disappoints,
            re-run with <span className="font-mono">Data type: video</span> or a
            higher quality.
          </span>
        </p>
      )}

      {fragmented && (
        <p className="flex gap-2 text-sm text-amber-300 bg-amber-950/20 border border-amber-800/60 rounded px-3 py-2">
          <Split className="w-4 h-4 mt-0.5 shrink-0" />
          <span>
            {result.sparse_models} sparse models — the capture broke into that
            many components. <span className="font-mono">sparse/0</span> is the
            largest and the only one step 4 trains on. Raise the overlap or
            switch to <span className="font-mono">Data type: video</span> if that
            loses too much of the scene.
          </span>
        </p>
      )}
    </div>
  );
};

const Step3_Sfm: React.FC = () => {
  const { currentProjectId, stepStatuses, setCurrentStep } = usePipelineStore();
  const { startPipeline } = usePipeline();
  const { defaults } = useDefaults();
  const { frames, summary, analysed } = useCuration(currentProjectId);

  const [showSettings, setShowSettings] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SfmResult | null>(null);
  const [masks, setMasks] = useState<MaskState | null>(null);

  const {
    value: sfm, setValue: setSfm, flush: flushSfm,
    saving, savedAt, error: saveError,
  } = useProjectSettings<SfmDefaults>(currentProjectId, 'sfm', defaults?.sfm ?? null);

  const status = stepStatuses[3];
  const isRunning = status === 'running';
  const isDone = status === 'done';

  const refresh = useCallback(() => {
    if (!currentProjectId) return;
    client.get(`/files/${currentProjectId}/sfm`)
      .then((r) => setResult(r.data?.sfm ?? null))
      .catch(() => setResult(null));
    client.get(`/files/${currentProjectId}/masks`)
      .then((r) => setMasks(r.data?.masks ?? null))
      .catch(() => setMasks(null));
  }, [currentProjectId]);

  // Re-read when the step finishes: `sfm_result.json` is written by the run, so
  // the panel is only ever as current as its last fetch.
  useEffect(() => { refresh(); }, [refresh, isDone]);

  const handleRun = async () => {
    if (!currentProjectId || !sfm) return;
    setError(null);
    // The run resets step 3 before it writes, so the previous verdict describes
    // a model that is already gone.
    setResult(null);
    try {
      await flushSfm();
      await startPipeline(currentProjectId, 3, { sfm });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to start the reconstruction');
    }
  };

  const frameCount = frames.length;
  const rejected = analysed && summary ? summary.total - summary.kept : 0;

  return (
    <div className="flex flex-col gap-6 p-6 max-w-4xl mx-auto">
      <h2 className="text-xl font-semibold text-slate-100">
        Step 3 — Structure from Motion
      </h2>

      {/* What the run will read, before it reads it — the same contract as
          step 2's source panel. */}
      <div className="rounded-lg bg-slate-800 border border-slate-700 px-4 py-3 space-y-2">
        <div className="flex gap-4 text-sm text-slate-300 flex-wrap">
          <span>
            Images:{' '}
            <span className="text-slate-100 font-medium">{frameCount} in frames/</span>
          </span>
          <span>
            Masks:{' '}
            <span className={masks?.state === 'ready' ? 'text-emerald-400 font-medium' : 'text-slate-500 font-medium'}>
              {masks?.state === 'ready' ? `${masks.matched} matched` : 'none'}
            </span>
          </span>
          <span>Quality: <span className="text-slate-100 font-medium">{sfm?.quality ?? '—'}</span></span>
          <span>Data type: <span className="text-slate-100 font-medium">{sfm?.data_type ?? '—'}</span></span>
          <span>Lens: <span className="text-slate-100 font-medium">{sfm?.camera_model ?? '—'}</span></span>
        </div>

        {/* The one thing about this step that surprises people. `sfm auto` reads
            the image *directory*, and CLAUDE.md §5.2 is why there is no second,
            filtered copy of it: step 4 trains on the same frames/. So a curation
            verdict is advisory until the frame is actually deleted. */}
        {rejected > 0 && (
          <p className="flex gap-2 text-xs text-slate-400 border-t border-slate-700/60 pt-2">
            <Info className="w-3.5 h-3.5 mt-0.5 shrink-0 text-cyan-500" />
            <span>
              Curation rejected {rejected} of {summary?.total} frames, and the
              reconstruction still reads all {frameCount} of them: it is handed
              the folder, and there is no second filtered copy of the images
              anywhere in this pipeline. Delete the rejected frames in step 2 if
              you want them out of the model.
            </span>
          </p>
        )}
      </div>

      <div className="flex items-center justify-end gap-1">
        <SaveState saving={saving} savedAt={savedAt} error={saveError} />
        <Button
          variant="ghost" size="sm"
          onClick={() => setShowSettings((v) => !v)}
          className="text-slate-400 hover:text-slate-100 gap-1"
        >
          <Sliders className="w-4 h-4" />
          Advanced
        </Button>
      </div>

      {showSettings && sfm && (
        <div className="rounded-lg bg-slate-800 border border-slate-700 p-4">
          <SfmSettings
            settings={sfm}
            maskCount={masks?.matched ?? 0}
            onChange={setSfm}
          />
        </div>
      )}

      {error && (
        <p className="text-sm text-red-400 bg-red-950/30 border border-red-800 rounded px-3 py-2">
          {error}
        </p>
      )}

      <div className="flex gap-2 flex-wrap">
        <Button
          onClick={handleRun}
          disabled={isRunning || !currentProjectId || !sfm || frameCount === 0}
          className="bg-cyan-600 hover:bg-cyan-500 text-white gap-1"
        >
          {isRunning ? 'Reconstructing…' : result ? (<><RefreshCw className="w-4 h-4" /> Re-run SfM</>) : 'Reconstruct'}
        </Button>
        {frameCount === 0 && (
          <span className="self-center text-xs text-slate-500">
            No frames yet — run step 2 first.
          </span>
        )}
      </div>

      {(isRunning || isDone) && (
        <ProgressBar step="sfm" label="Extract → match → map" />
      )}

      {result && <SfmReport result={result} />}

      {/* The cloud and the camera path. Several failures are visible here and
          nowhere else: a path that folded back on itself, a component sitting
          at another scale, a reconstruction of something other than the scene
          you shot (CLAUDE.md §7.9). `refreshKey` is the step status, so the run
          finishing is what re-checks the source. */}
      {(isDone || result) && currentProjectId && (
        <SceneViewer
          projectId={currentProjectId}
          source="sfm"
          refreshKey={status ?? 'idle'}
          withCameras
        />
      )}

      {isDone && result && (
        <div className="flex items-center justify-between">
          <span className="text-sm text-green-400 font-medium">
            {result.registered !== undefined
              ? `${result.registered} cameras in sparse/0`
              : 'Reconstruction complete'}
          </span>
          <Button
            onClick={() => setCurrentStep(4)}
            className="bg-green-700 hover:bg-green-600 text-white gap-1"
          >
            <CheckCircle className="w-4 h-4" />
            Validate &amp; Continue to Training
          </Button>
        </div>
      )}
    </div>
  );
};

export default Step3_Sfm;
