import React, { useCallback, useEffect, useState } from 'react';
import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import {
  AlertTriangle, CheckCircle, Info, Mountain, RefreshCw, Sliders,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { usePipelineStore } from '@/store/pipelineStore';
import { usePipeline } from '@/hooks/usePipeline';
import { useDefaults } from '@/hooks/useDefaults';
import { useProjectSettings } from '@/hooks/useProjectSettings';
import { ProgressBar } from '@/components/panels/ProgressBar';
import SceneViewer from '@/components/viewer/SceneViewer';
import TrainSettings, { presetDefaults } from '@/components/settings/TrainSettings';
import GeometrySettings from '@/components/settings/GeometrySettings';
import SaveState from '@/components/settings/SaveState';
import SplatExportPanel from '@/components/panels/SplatExportPanel';
import { useSplatExport } from '@/hooks/useSplatExport';
import client from '@/api/client';
import type {
  GeometryDefaults, GeometryRunResult, TrainDefaults, TrainMetric, TrainResult,
} from '@/types';

/** What `--data` and `--image-dir` will really point at, read off the folders. */
interface DatasetState {
  has_model: boolean;
  images: number;
  depths: number;
  normals: number;
}

interface MaskState {
  masks: number;
  matched: number;
  state: 'none' | 'ready' | 'unmatched';
}

const Stat: React.FC<{ label: string; value: string; tone?: string; hint?: string }> = ({
  label, value, tone = 'text-slate-100', hint,
}) => (
  <div className="flex flex-col" title={hint}>
    <span className={`text-lg font-semibold ${tone}`}>{value}</span>
    <span className="text-[11px] text-slate-500 uppercase tracking-wide">{label}</span>
  </div>
);

function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(0)} MB`;
  return `${(bytes / 1024).toFixed(0)} KB`;
}

/**
 * The training chart, from the bar line `spirula train` prints every 100 steps.
 *
 * Three series on two axes, because they read on utterly different scales: the
 * image loss and SSIM sit in 0–1 on the left, PSNR in the twenties on the right.
 * The splat count is deliberately not drawn here — it is six orders of magnitude
 * away from the rest and belongs in the report strip, which is where it is.
 */
const TrainChart: React.FC<{ points: TrainMetric[] }> = ({ points }) => {
  if (points.length < 2) return null;
  return (
    <div className="rounded-lg bg-slate-800 border border-slate-700 p-3">
      <ResponsiveContainer width="100%" height={180}>
        <LineChart data={points} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
          <CartesianGrid stroke="#334155" strokeDasharray="3 3" />
          <XAxis
            dataKey="iteration" stroke="#64748b" tick={{ fontSize: 10 }}
            type="number" domain={['dataMin', 'dataMax']}
          />
          <YAxis yAxisId="unit" stroke="#64748b" tick={{ fontSize: 10 }} domain={[0, 1]} />
          <YAxis
            yAxisId="db" orientation="right" stroke="#64748b"
            tick={{ fontSize: 10 }} domain={['auto', 'auto']}
          />
          <Tooltip
            contentStyle={{
              background: '#020617', border: '1px solid #334155',
              borderRadius: 4, fontSize: 12,
            }}
            labelFormatter={(v) => `step ${v}`}
          />
          <Line
            yAxisId="unit" type="monotone" dataKey="loss" name="rgb_loss"
            stroke="#ef4444" dot={false} isAnimationActive={false} connectNulls
          />
          <Line
            yAxisId="unit" type="monotone" dataKey="ssim" name="ssim"
            stroke="#06b6d4" dot={false} isAnimationActive={false} connectNulls
          />
          <Line
            yAxisId="db" type="monotone" dataKey="psnr" name="psnr"
            stroke="#22c55e" dot={false} isAnimationActive={false} connectNulls
          />
        </LineChart>
      </ResponsiveContainer>
      <p className="text-[11px] text-slate-500 mt-1">
        <span className="text-red-400">rgb_loss</span> and{' '}
        <span className="text-cyan-400">ssim</span> on the left (0–1),{' '}
        <span className="text-green-400">psnr</span> in dB on the right.
      </p>
    </div>
  );
};

/**
 * How the last run went, persisted rather than scrolled past.
 *
 * The trainer prints its numbers on a line every 100 steps and the LiveLog keeps
 * 500 of them, so a 30 000-iteration run's own final PSNR is out of the buffer
 * long before anybody asks what it was. `train/train_result.json` is where it
 * lives instead.
 */
const TrainReport: React.FC<{ result: TrainResult }> = ({ result }) => (
  <div className="space-y-3">
    <div className="rounded-lg bg-slate-800 border border-slate-700 px-4 py-3">
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-4">
        <Stat
          label="steps"
          value={(result.steps ?? result.iteration)?.toLocaleString() ?? '—'}
          hint={`${result.iterations_requested.toLocaleString()} requested`}
        />
        {/*
          The written file, not the last bar line. `num_gaussians` is the live
          count and the final prune runs after it, so the two differ by ~28 %
          on a capped run — the card used to claim exactly the cap for a file
          that held a quarter fewer. The cap still colours the number, because
          reaching it during training is what the warning below is about.
        */}
        <Stat
          label="splats"
          value={(result.splat_count ?? result.num_gaussians)?.toLocaleString() ?? '—'}
          tone={result.num_gaussians && result.num_gaussians >= result.cap_max
            ? 'text-amber-400' : 'text-slate-100'}
          hint={result.splat_count && result.num_gaussians
            && result.num_gaussians !== result.splat_count
            ? `${result.num_gaussians.toLocaleString()} at the cap, then pruned`
            : `cap ${result.cap_max.toLocaleString()}`}
        />
        <Stat
          label="psnr"
          value={result.psnr !== undefined ? `${result.psnr.toFixed(2)} dB` : '—'}
        />
        <Stat
          label="ssim"
          value={result.ssim !== undefined ? result.ssim.toFixed(3) : '—'}
          hint={result.loss !== undefined ? `rgb_loss ${result.loss.toFixed(5)}` : undefined}
        />
        <Stat
          label="splat.ply"
          value={result.splat_bytes ? formatBytes(result.splat_bytes) : '—'}
          hint={result.splat_path ?? undefined}
        />
      </div>
      <p className="text-xs text-slate-500 mt-2">
        exit {result.exit_code}
        {result.elapsed_s !== undefined && ` · ${result.elapsed_s.toFixed(0)} s`}
        {` · preset ${result.preset} · quality ${result.quality}`}
        {` · ${result.primitive} · sh ${result.sh_degree}`}
        {` · ${result.masks_used ? `${result.mask_count} masks, trained as empty` : 'no masks'}`}
        {(result.normals_used || result.depths_used)
          && ` · ${[result.normals_used && 'normals', result.depths_used && 'depths']
            .filter(Boolean).join(' + ')}`}
        {` · spirula ${result.spirula_version}`}
      </p>
    </div>

    {result.num_gaussians !== undefined && result.num_gaussians >= result.cap_max && (
      <p className="flex gap-2 text-sm text-amber-300 bg-amber-950/20 border border-amber-800/60 rounded px-3 py-2">
        <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
        <span>
          The run finished at its splat cap ({result.cap_max.toLocaleString()}),
          so densification was still being refused detail it wanted. Raise
          <span className="font-mono"> Max splats </span> if the viewer looks
          under-resolved — and remember the reference 998 463-splat run wrote a
          247 MB PLY.
        </span>
      </p>
    )}
  </div>
);

const Step4_Train: React.FC = () => {
  const {
    currentProjectId, stepStatuses, setCurrentStep, trainMetrics, clearTrainMetrics,
  } = usePipelineStore();
  const { startPipeline, runGeometry } = usePipeline();
  const { defaults } = useDefaults();

  const [showSettings, setShowSettings] = useState(false);
  const [showGeometry, setShowGeometry] = useState(false);
  const [geometryRun, setGeometryRun] = useState<GeometryRunResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TrainResult | null>(null);
  const [dataset, setDataset] = useState<DatasetState | null>(null);
  const [masks, setMasks] = useState<MaskState | null>(null);

  const {
    value: train, setValue: setTrain, flush: flushTrain,
    saving, savedAt, error: saveError,
  } = useProjectSettings<TrainDefaults>(currentProjectId, 'train', defaults?.train ?? null);

  // The geometry pass is layer 3 like everything else, and it attaches to step 4
  // without being it: it writes per-image maps *into* the dataset step 3 built,
  // and running it never marks this step done (CLAUDE.md §7.5).
  const {
    value: geometry, setValue: setGeometry, flush: flushGeometry,
    saving: geoSaving, savedAt: geoSavedAt, error: geoSaveError,
  } = useProjectSettings<GeometryDefaults>(
    currentProjectId, 'geometry', defaults?.geometry ?? null,
  );

  const status = stepStatuses[4];
  const isRunning = status === 'running';
  const isDone = status === 'done';

  // The deliverable export (CLAUDE.md §7.6c) — the fourth pass attached to this
  // step, and the one whose output no later step reads. Enabled by there being
  // a splat rather than by a toggle: it renders at the very bottom of the page,
  // after the viewer and the crop panel inside it, because exporting is the
  // last thing done to a run and it reads whatever the crop left behind.
  //
  // `status` is what re-reads it. The crop is a pass attached to this same step,
  // so applying one takes step 4 through `running` and back
  // (`_run_attached_pass`) — which is exactly the cue for this panel to notice
  // that `resolve_splat` now answers `train/crop/splat.ply` and to say so.
  const splatExport = useSplatExport(currentProjectId, Boolean(result || isDone), status);

  const refresh = useCallback(() => {
    if (!currentProjectId) return;
    client.get(`/files/${currentProjectId}/train`)
      .then((r) => {
        setResult(r.data?.train ?? null);
        setDataset(r.data?.dataset ?? null);
      })
      .catch(() => { setResult(null); setDataset(null); });
    // The geometry pass writes its own result beside the sparse model, for the
    // same reason every step does: a log line is gone on the next page load.
    client.get(`/files/${currentProjectId}/geometry`)
      .then((r) => setGeometryRun(r.data?.run ?? null))
      .catch(() => setGeometryRun(null));
    client.get(`/files/${currentProjectId}/masks`)
      .then((r) => setMasks(r.data?.masks ?? null))
      .catch(() => setMasks(null));
  }, [currentProjectId]);

  useEffect(() => { refresh(); }, [refresh, isDone]);

  const handleRun = async () => {
    if (!currentProjectId || !train) return;
    setError(null);
    // The run resets step 4 before it writes, so the previous verdict and the
    // previous chart both describe a splat that is already gone.
    setResult(null);
    clearTrainMetrics();
    try {
      await flushTrain();
      await startPipeline(currentProjectId, 4, { train });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to start the training');
    }
  };

  const handleGeometry = async () => {
    if (!currentProjectId || !geometry) return;
    setError(null);
    try {
      await flushGeometry();
      await runGeometry(currentProjectId, { geometry });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to start the geometry pass');
    }
  };

  const preset = train ? presetDefaults(train.preset) : null;
  const iterations = train?.num_iterations ?? (preset?.num_iterations as number | undefined);
  const capMax = train?.cap_max ?? (preset?.cap_max as number | undefined);
  const ready = Boolean(dataset?.has_model && dataset.images > 0);
  const maskCount = masks?.state === 'ready' ? masks.matched : 0;

  return (
    <div className="flex flex-col gap-6 p-6 max-w-4xl mx-auto">
      <h2 className="text-xl font-semibold text-slate-100">
        Step 4 — Gaussian Splat training
      </h2>

      {/* What the run will read, before it reads it. The dataset is step 3's
          workspace and the images are the same frames/ step 3 reconstructed —
          there is no second, filtered copy of them anywhere (CLAUDE.md §5.2). */}
      <div className="rounded-lg bg-slate-800 border border-slate-700 px-4 py-3 space-y-2">
        <div className="flex gap-4 text-sm text-slate-300 flex-wrap">
          <span>
            Dataset:{' '}
            <span className={dataset?.has_model ? 'text-slate-100 font-medium' : 'text-amber-400 font-medium'}>
              {dataset?.has_model ? 'sfm/sparse/0' : 'no sparse model'}
            </span>
          </span>
          <span>
            Images:{' '}
            <span className="text-slate-100 font-medium">
              {dataset?.images ?? 0} in frames/
            </span>
          </span>
          <span>
            Masks:{' '}
            <span className={maskCount ? 'text-emerald-400 font-medium' : 'text-slate-500 font-medium'}>
              {maskCount ? `${maskCount} matched` : 'none'}
            </span>
          </span>
          <span>
            Geometry:{' '}
            <span className={dataset && (dataset.normals || dataset.depths)
              ? 'text-emerald-400 font-medium' : 'text-slate-500 font-medium'}>
              {dataset && (dataset.normals || dataset.depths)
                ? [dataset.normals && `${dataset.normals} normals`,
                   dataset.depths && `${dataset.depths} depths`].filter(Boolean).join(' + ')
                : 'none'}
            </span>
          </span>
        </div>
        <div className="flex gap-4 text-sm text-slate-300 flex-wrap">
          <span>Preset: <span className="text-slate-100 font-medium">{train?.preset ?? '—'}</span></span>
          <span>Iterations: <span className="text-slate-100 font-medium">{iterations?.toLocaleString() ?? '—'}</span></span>
          <span>Cap: <span className="text-slate-100 font-medium">{capMax?.toLocaleString() ?? '—'}</span></span>
        </div>

        {!dataset?.has_model && (
          <p className="flex gap-2 text-xs text-amber-300 border-t border-slate-700/60 pt-2">
            <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
            <span>
              No sparse model under <span className="font-mono">sfm/</span>. Run
              step 3 first — the trainer probes{' '}
              <span className="font-mono">sparse/0</span>,{' '}
              <span className="font-mono">colmap/sparse/0</span>,{' '}
              <span className="font-mono">sparse</span>,{' '}
              <span className="font-mono">colmap</span> and the dataset folder
              itself, and none of them holds one.
            </span>
          </p>
        )}

        {maskCount > 0 && (
          <p className="flex gap-2 text-xs text-slate-400 border-t border-slate-700/60 pt-2">
            <Info className="w-3.5 h-3.5 mt-0.5 shrink-0 text-cyan-500" />
            <span>
              The masks are trained as empty space
              (<span className="font-mono">--apply-loss-for-mask 1</span>), which
              removes the background and leaves the subject. The other position
              only drops the masked pixels from the loss and measured as
              indistinguishable from no masks at all, so it is not offered.
            </span>
          </p>
        )}
      </div>

      <div className="flex items-center justify-end gap-1">
        <SaveState
          saving={saving || geoSaving}
          savedAt={geoSavedAt ?? savedAt}
          error={saveError ?? geoSaveError}
        />
        <Button
          variant="ghost" size="sm"
          onClick={() => setShowGeometry((v) => !v)}
          className="text-slate-400 hover:text-slate-100 gap-1"
        >
          <Mountain className="w-4 h-4" />
          Geometry
        </Button>
        <Button
          variant="ghost" size="sm"
          onClick={() => setShowSettings((v) => !v)}
          className="text-slate-400 hover:text-slate-100 gap-1"
        >
          <Sliders className="w-4 h-4" />
          Advanced
        </Button>
      </div>

      {/* The geometry pass — a separate run, never a re-training (CLAUDE.md
          §7.5). It writes sfm/normals/ and sfm/depths/ *inside* the dataset, so
          the training below reads them through --data with no flag at all. A
          step 3 re-run deletes them with the rest of sfm/. */}
      {showGeometry && geometry && (
        <div className="rounded-lg bg-slate-800 border border-slate-700 p-4 space-y-4">
          <GeometrySettings settings={geometry} onChange={setGeometry} />

          <div className="flex items-center gap-3 flex-wrap border-t border-slate-700/60 pt-4">
            <Button
              onClick={handleGeometry}
              disabled={isRunning || !dataset?.has_model}
              className="bg-slate-700 hover:bg-slate-600 text-white gap-1"
            >
              <Mountain className="w-4 h-4" />
              {dataset && (dataset.normals || dataset.depths)
                ? 'Re-run the geometry pass' : 'Estimate depth & normals'}
            </Button>
            <span className="text-xs text-slate-500">
              {dataset?.has_model
                ? 'Writes sfm/normals/ and sfm/depths/ — the training is not re-run.'
                : "Needs step 3's sparse model: the pass reads the reconstruction's cameras."}
            </span>
          </div>

          {geometryRun && (
            <div className="rounded-md bg-slate-900/60 border border-slate-700 px-3 py-2 text-xs text-slate-400 space-y-1">
              <p>
                Last run: {geometryRun.normals} normal
                {geometryRun.depth ? ` + ${geometryRun.depths} depth` : ''} maps
                {' · '}{geometryRun.normal_format} at {geometryRun.max_size} px
                {geometryRun.elapsed_s !== undefined && ` · ${geometryRun.elapsed_s} s`}
                {' · '}spirula {geometryRun.spirula_version}
              </p>
              {geometryRun.skipped_images > 0 && (
                <p className="text-amber-300/90">
                  {geometryRun.skipped_images} image(s) were skipped — those
                  frames carry no geometry term.
                </p>
              )}
              {geometryRun.stale_normals > 0 && (
                <p className="text-amber-300/90">
                  {geometryRun.stale_normals} map(s) in the other format are
                  still beside these. The tool writes the new format next to the
                  old rather than over it — delete the stale ones.
                </p>
              )}
            </div>
          )}

          {(isRunning || geometryRun) && (
            <ProgressBar step="geometry" label="spirula geometry" />
          )}
        </div>
      )}

      {showSettings && train && (
        <div className="rounded-lg bg-slate-800 border border-slate-700 p-4">
          <TrainSettings
            settings={train}
            maskCount={maskCount}
            hasDepths={Boolean(dataset?.depths)}
            hasNormals={Boolean(dataset?.normals)}
            onChange={setTrain}
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
          disabled={isRunning || !currentProjectId || !train || !ready}
          className="bg-cyan-600 hover:bg-cyan-500 text-white gap-1"
        >
          {isRunning ? 'Training…' : result ? (<><RefreshCw className="w-4 h-4" /> Re-train</>) : 'Train'}
        </Button>
        {!ready && (
          <span className="self-center text-xs text-slate-500">
            Nothing to train on yet — run step 3 first.
          </span>
        )}
      </div>

      {(isRunning || isDone) && (
        <ProgressBar step="train" label="Load → train → checkpoint" />
      )}

      {trainMetrics.length > 1 && <TrainChart points={trainMetrics} />}

      {result && <TrainReport result={result} />}

      {/* The splat. 247 MB for a small project, so nothing here loads the source
          file: `core/ply.py` streams it into a decimated `.splat` under
          preview/ and the viewer opens that (§7.9).

          `withCrop` is what puts the box/sphere volumes on it (§7.6b). It rides
          here rather than on step 5 because the thing being cut is the trained
          splat, and because the cut has to happen before the mesh is extracted
          from it — but nothing about it re-trains: it is a pass of its own, like
          the geometry one above.

          `withViewpoint` is the toolbar's "Save view" (§7.6d): the camera this
          scene is worth being seen from, stored in the dataset frame beside the
          crop volumes and carried into — or beside — whatever the panel below
          exports. It rides on this viewer for the crop's reason, which is that
          this is where the splat is actually looked at. */}
      {(isDone || result) && currentProjectId && (
        <SceneViewer
          projectId={currentProjectId}
          source="train"
          refreshKey={status ?? 'idle'}
          withCameras
          withCrop
          withViewpoint
        />
      )}

      {/* The export, last on the page and deliberately so (§7.6c).
          Everything above it produces the splat: the run, then the crop that
          trims it inside the viewer. This is the only thing here that produces
          a *file for somebody else*, it reads whatever those two left — the
          crop when there is one, through `resolve_splat` — and nothing after it
          reads what it writes. Putting it above the viewer, as it first was,
          asked the user to choose an export format before they had seen the
          scene or cut it. */}
      {(isDone || result) && currentProjectId && (
        <>
          <SplatExportPanel tool={splatExport} disabled={isRunning} />
          {splatExport.running && (
            <ProgressBar step="splat_export" label="Reduce → write → convert" />
          )}
        </>
      )}

      {isDone && result && (
        <div className="flex items-center justify-between">
          <span className="text-sm text-green-400 font-medium">
            {(result.splat_count ?? result.num_gaussians) !== undefined
              && (result.splat_count ?? result.num_gaussians) !== null
              ? `${(result.splat_count ?? result.num_gaussians)!.toLocaleString()} splats written`
              : 'Training complete'}
          </span>
          <Button
            onClick={() => setCurrentStep(5)}
            className="bg-green-700 hover:bg-green-600 text-white gap-1"
          >
            <CheckCircle className="w-4 h-4" />
            Validate &amp; Continue to Mesh
          </Button>
        </div>
      )}
    </div>
  );
};

export default Step4_Train;
