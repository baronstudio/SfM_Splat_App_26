import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle, CheckCircle, Info, RefreshCw, Sliders,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { usePipelineStore } from '@/store/pipelineStore';
import { usePipeline } from '@/hooks/usePipeline';
import { useDefaults } from '@/hooks/useDefaults';
import { useProjectSettings } from '@/hooks/useProjectSettings';
import { ProgressBar } from '@/components/panels/ProgressBar';
import SceneViewer from '@/components/viewer/SceneViewer';
import MeshSettings, { meshRefusal } from '@/components/settings/MeshSettings';
import SaveState from '@/components/settings/SaveState';
import client, { staticUrl } from '@/api/client';
import type { MeshDefaults, MeshFile, MeshInputState, MeshResult } from '@/types';

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
 * How the last meshing run went, persisted rather than scrolled past.
 *
 * The run prints 419 lines and 360 of them are one camera counter, so the four
 * that carry the answer are out of a 500-line LiveLog by the time anybody looks
 * for them. `mesh/mesh_result.json` is where they live instead — the same
 * argument steps 3 and 4 make for their own result files.
 */
const MeshReport: React.FC<{ result: MeshResult }> = ({ result }) => {
  const openBoundary = (result.boundary_edges ?? 0) > 0;
  const nonManifold = (result.non_manifold_edges ?? 0) > 0;

  return (
    <div className="space-y-3">
      <div className="rounded-lg bg-slate-800 border border-slate-700 px-4 py-3">
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-4">
          <Stat label="vertices" value={result.vertices?.toLocaleString() ?? '—'} />
          <Stat label="faces" value={result.faces?.toLocaleString() ?? '—'} />
          <Stat
            label="components"
            value={result.components?.toLocaleString() ?? '—'}
            hint="Connected pieces. A surface extracted from gaussians is never one shell — the reference mesh ended on 5111."
          />
          <Stat
            label="texture"
            value={result.texture_size ? `${result.texture_size} px` : '—'}
            hint={result.texel_coverage_pct !== undefined
              ? `${result.texel_coverage_pct.toFixed(1)} % of the atlas carries a texel`
              : undefined}
          />
          <Stat
            label="coverage"
            value={result.texel_coverage_pct !== undefined
              ? `${result.texel_coverage_pct.toFixed(1)} %`
              : '—'}
            tone={result.texel_coverage_pct !== undefined && result.texel_coverage_pct < 5
              ? 'text-amber-400' : 'text-slate-100'}
            hint={result.texels_covered && result.texels_total
              ? `${result.texels_covered.toLocaleString()} of ${result.texels_total.toLocaleString()} texels`
              : undefined}
          />
        </div>
        <p className="text-xs text-slate-500 mt-2">
          exit {result.exit_code}
          {result.elapsed_s !== undefined && ` · ${result.elapsed_s.toFixed(1)} s`}
          {` · ${result.formats.join(', ')} · ${result.color}`}
          {result.cameras_used !== undefined
            && ` · ${result.cameras_used} cameras`}
          {!result.cameras_requested && ' · --no-data'}
          {result.gaussians !== undefined && ` · from ${result.gaussians.toLocaleString()} gaussians`}
          {` · spirula ${result.spirula_version}`}
        </p>
      </div>

      {result.files.length > 0 && (
        <div className="rounded-lg bg-slate-800 border border-slate-700 px-4 py-3">
          <p className="text-[11px] uppercase tracking-wide text-slate-500 mb-2">
            mesh/
          </p>
          <ul className="space-y-1">
            {result.files.map((f: MeshFile) => (
              <li key={f.filename} className="flex justify-between text-sm">
                <span className="font-mono text-slate-300">{f.filename}</span>
                <span className="text-slate-500">{formatBytes(f.bytes)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {(openBoundary || nonManifold) && (
        <p className="flex gap-2 text-sm text-amber-300 bg-amber-950/20 border border-amber-800/60 rounded px-3 py-2">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          <span>
            {openBoundary && (
              <>
                {result.boundary_edges!.toLocaleString()} boundary edges — the
                surface is open where no camera saw it, which is what{' '}
                <span className="font-mono">--cull-unseen</span> leaves behind.
                Turn it off, or capture the sides that are missing.
              </>
            )}
            {openBoundary && nonManifold && ' '}
            {nonManifold && (
              <>
                {result.non_manifold_edges!.toLocaleString()} non-manifold edges
                — some downstream tools refuse a mesh with these.
              </>
            )}
          </span>
        </p>
      )}
    </div>
  );
};

const Step5_Mesh: React.FC = () => {
  const { currentProjectId, stepStatuses } = usePipelineStore();
  const { startPipeline } = usePipeline();
  const { defaults } = useDefaults();

  const [showSettings, setShowSettings] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<MeshResult | null>(null);
  const [input, setInput] = useState<MeshInputState | null>(null);
  const [exported, setExported] = useState<MeshFile[]>([]);

  const {
    value: mesh, setValue: setMesh, flush: flushMesh,
    saving, savedAt, error: saveError,
  } = useProjectSettings<MeshDefaults>(currentProjectId, 'mesh', defaults?.mesh ?? null);

  const status = stepStatuses[5];
  const isRunning = status === 'running';
  const isDone = status === 'done';

  const refresh = useCallback(() => {
    if (!currentProjectId) return;
    client.get(`/files/${currentProjectId}/mesh`)
      .then((r) => {
        setResult(r.data?.mesh ?? null);
        setInput(r.data?.input ?? null);
      })
      .catch(() => { setResult(null); setInput(null); });
    client.get(`/files/${currentProjectId}/export`)
      .then((r) => setExported(r.data?.files ?? []))
      .catch(() => setExported([]));
  }, [currentProjectId]);

  useEffect(() => { refresh(); }, [refresh, isDone]);

  const handleRun = async () => {
    if (!currentProjectId || !mesh) return;
    setError(null);
    // The run resets step 5 before it writes, so the previous verdict describes
    // a mesh that is already gone.
    setResult(null);
    setExported([]);
    try {
      await flushMesh();
      await startPipeline(currentProjectId, 5, { mesh });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to start the meshing');
    }
  };

  const refusal = mesh ? meshRefusal(mesh) : null;
  const ready = Boolean(input?.has_splat && (!mesh?.use_cameras || input?.has_model));

  return (
    <div className="flex flex-col gap-6 p-6 max-w-4xl mx-auto">
      <h2 className="text-xl font-semibold text-slate-100">
        Step 5 — Surface mesh
      </h2>

      {/* What the run will read, before it reads it — the same contract steps 2,
          3 and 4 keep. The mesh is extracted from the gaussians, and the
          cameras are what decide occupancy and colour (CLAUDE.md §7.8). */}
      <div className="rounded-lg bg-slate-800 border border-slate-700 px-4 py-3 space-y-2">
        <div className="flex gap-4 text-sm text-slate-300 flex-wrap">
          <span>
            Checkpoint:{' '}
            <span className={input?.has_splat ? 'text-slate-100 font-medium' : 'text-amber-400 font-medium'}>
              {input?.has_splat ? input.checkpoint : 'no trained splat'}
            </span>
            {input?.splat_bytes ? (
              <span className="text-slate-500"> · {formatBytes(input.splat_bytes)}</span>
            ) : null}
          </span>
          <span>
            Cameras:{' '}
            <span className={input?.has_model ? 'text-slate-100 font-medium' : 'text-slate-500 font-medium'}>
              {input?.has_model ? 'sfm/sparse/0' : 'no sparse model'}
            </span>
          </span>
          <span>
            Output:{' '}
            <span className="text-slate-100 font-medium">
              {mesh ? `${mesh.formats.join(', ') || 'none'} · ${mesh.color}` : '—'}
            </span>
          </span>
        </div>

        {!input?.has_splat && (
          <p className="flex gap-2 text-xs text-amber-300 border-t border-slate-700/60 pt-2">
            <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
            <span>
              No <span className="font-mono">splat.ply</span> under{' '}
              <span className="font-mono">train/</span>. Run step 4 first — the
              mesh is extracted from the gaussians, not from the sparse model.
            </span>
          </p>
        )}

        {input?.has_splat && mesh?.use_cameras && !input.has_model && (
          <p className="flex gap-2 text-xs text-amber-300 border-t border-slate-700/60 pt-2">
            <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
            <span>
              No sparse model under <span className="font-mono">sfm/</span>, and
              the cameras are what decide occupancy and colour. Run step 3, or
              turn off “Use the cameras” to mesh from the gaussian densities
              alone.
            </span>
          </p>
        )}

        <p className="flex gap-2 text-xs text-slate-400 border-t border-slate-700/60 pt-2">
          <Info className="w-3.5 h-3.5 mt-0.5 shrink-0 text-cyan-500" />
          <span>
            Step 5 also fills <span className="font-mono">export/</span> with
            the splat it meshed and the mesh it wrote — so re-running it clears
            that too. The exported files are hard links, not copies.
          </span>
        </p>
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

      {showSettings && mesh && (
        <div className="rounded-lg bg-slate-800 border border-slate-700 p-4">
          <MeshSettings settings={mesh} onChange={setMesh} />
        </div>
      )}

      {/* The refusal is shown even with the panel closed: it is the one setting
          that stops the run, and the tool's answer to it is exit 1 with nothing
          written at all — not even the formats it could have made. */}
      {refusal && (
        <p className="flex gap-2 text-sm text-red-300 bg-red-950/30 border border-red-800 rounded px-3 py-2">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          <span>{refusal}</span>
        </p>
      )}

      {error && (
        <p className="text-sm text-red-400 bg-red-950/30 border border-red-800 rounded px-3 py-2">
          {error}
        </p>
      )}

      <div className="flex gap-2 flex-wrap">
        <Button
          onClick={handleRun}
          disabled={isRunning || !currentProjectId || !mesh || !ready || Boolean(refusal)}
          className="bg-cyan-600 hover:bg-cyan-500 text-white gap-1"
        >
          {isRunning ? 'Meshing…' : result ? (<><RefreshCw className="w-4 h-4" /> Re-mesh</>) : 'Extract mesh'}
        </Button>
        {!ready && (
          <span className="self-center text-xs text-slate-500">
            Nothing to mesh yet — run step 4 first.
          </span>
        )}
      </div>

      {(isRunning || isDone) && (
        <>
          <ProgressBar step="mesh" label="1. Meshing" />
          <ProgressBar step="export" label="2. Export" />
        </>
      )}

      {result && <MeshReport result={result} />}

      {/* The mesh itself. Unlike the cloud and the splat there is no decimated
          copy: `GLTFLoader` reads the glb the tool wrote (CLAUDE.md §7.9). */}
      {(isDone || result) && currentProjectId && (
        <SceneViewer
          projectId={currentProjectId}
          source="mesh"
          refreshKey={status ?? 'idle'}
          withCameras
        />
      )}

      {exported.length > 0 && (
        <div className="rounded-lg bg-slate-800 border border-slate-700 px-4 py-3">
          <p className="text-[11px] uppercase tracking-wide text-slate-500 mb-2">
            export/ — the deliverables of this run
          </p>
          <ul className="space-y-1">
            {exported.map((f) => (
              <li key={f.filename} className="flex justify-between text-sm">
                <a
                  href={staticUrl(f.url ?? '')}
                  download={f.filename}
                  className="font-mono text-cyan-400 hover:text-cyan-300"
                >
                  {f.filename}
                </a>
                <span className="text-slate-500">{formatBytes(f.bytes)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Step 5 is the last step: there is nowhere to continue to, so the
          hand-off button is replaced by what the run produced. */}
      {isDone && result && (
        <div className="flex items-center gap-2 text-sm text-green-400 font-medium">
          <CheckCircle className="w-4 h-4 shrink-0" />
          <span>
            {result.faces !== undefined
              ? `${result.faces.toLocaleString()} faces written — pipeline complete.`
              : 'Mesh complete — pipeline complete.'}
          </span>
        </div>
      )}
    </div>
  );
};

export default Step5_Mesh;
