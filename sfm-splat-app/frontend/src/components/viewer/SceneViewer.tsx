import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Boxes, Camera, Download, FlipVertical2, Grid3x3, RefreshCw, RotateCcw, Route,
  Maximize2, AlertTriangle, Bookmark, BookmarkPlus, X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { staticUrl } from '@/api/client';
import { useDefaults } from '@/hooks/useDefaults';
import { useCameras, usePreview } from '@/hooks/usePreview';
import { useCrop } from '@/hooks/useCrop';
import { useViewpoint } from '@/hooks/useViewpoint';
import CropPanel from '@/components/panels/CropPanel';
import PointCloudCanvas from './PointCloudCanvas';
import SplatCanvas, { type ViewpointApi } from './SplatCanvas';
import { describeViewpoint, type Viewpoint } from './viewpoint';
import MeshCanvas from './MeshCanvas';
import type { PreviewSource } from '@/types';

/**
 * The 3D preview mounted in steps 3, 4 and 5.
 *
 * It picks the renderer from what the file *is*, not from which step asked:
 * a step can produce either a plain sparse cloud or a gaussian PLY, so keying
 * the viewer on the step number would sometimes pick the wrong renderer.
 *
 * Three renderers now — `PointCloudCanvas`, `SplatCanvas` and `MeshCanvas` —
 * and the third one is the odd one out in exactly one way: a glTF mesh has no
 * decimated preview to open at a level, because there is no record format to
 * decimate it into, so the level selector is hidden for it and the file the
 * tool wrote is what loads (`core/preview.py`).
 *
 * There is no per-source frame correction any more. The sparse cloud, the
 * trained splat and the camera overlay are all in one +Z-up frame — measured,
 * not assumed (CLAUDE.md §7.3) — so the single `Rx-90` of `frame.ts` sits on
 * the scene root of whichever canvas is mounted, and "Flip up" is a question
 * about the capture rather than a repair of a convention mismatch.
 */

interface SceneViewerProps {
  projectId: string;
  source: PreviewSource;
  /** Re-check the source when this changes — pass the step status. */
  refreshKey?: string | number;
  /** Offer the camera overlay. Off for steps where the poses say nothing new. */
  withCameras?: boolean;
  height?: number;
  /**
   * Offer the crop tool (CLAUDE.md §7.6b). Step 4 only, and only over a splat:
   * the cut is defined on gaussian centres and written by a pass over
   * `splat.ply`, so there is nothing for it to mean on a sparse cloud or a mesh.
   */
  withCrop?: boolean;
  /**
   * Offer "Save view" (CLAUDE.md §7.6d). Step 4 only, and only over a splat:
   * what the saved camera is *for* is the export of that splat, which carries
   * it in the PLY header or in a sidecar beside every other format.
   */
  withViewpoint?: boolean;
}

const LEVELS = [250_000, 1_000_000, 2_000_000];

function formatCount(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value % 1_000_000 ? 1 : 0)}M`;
  if (value >= 1_000) return `${Math.round(value / 1_000)}k`;
  return String(value);
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${bytes} B`;
}

export const SceneViewer: React.FC<SceneViewerProps> = ({
  projectId, source, refreshKey, withCameras = true, height = 420,
  withCrop = false, withViewpoint = false,
}) => {
  const { defaults } = useDefaults();
  const viewerDefaults = defaults?.viewer;

  const [level, setLevel] = useState<number | null>(null);
  const [showCameras, setShowCameras] = useState(true);
  const [showPath, setShowPath] = useState(true);
  const [pointSize, setPointSize] = useState(1.6);
  const [loadPercent, setLoadPercent] = useState<number | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [viewNonce, setViewNonce] = useState(0);
  const [upFlipped, setUpFlipped] = useState(false);
  const [wireframe, setWireframe] = useState(false);

  // Hydrate from defaults.json once it lands; the state above is the pre-fetch
  // placeholder, exactly like the Advanced panels do it (CLAUDE.md §4).
  useEffect(() => {
    if (!viewerDefaults) return;
    setLevel((current) => (current === null ? viewerDefaults.preview_max_points : current));
    setShowCameras(viewerDefaults.show_cameras);
    setShowPath(viewerDefaults.show_camera_path);
    setPointSize(viewerDefaults.point_size);
  }, [viewerDefaults]);

  const effectiveLevel = level ?? 1_000_000;
  const { state, error, refresh } = usePreview(projectId, source, effectiveLevel);
  const { cameras, refresh: refreshCameras } = useCameras(projectId, withCameras);

  // A finished step writes new files; the preview and the poses built from the
  // previous ones are stale, and the backend only notices when asked. Skipped
  // on mount — the hooks already fetch once on their own.
  const mounted = useRef(false);
  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true;
      return;
    }
    refresh();
    refreshCameras();
  }, [refreshKey, refresh, refreshCameras]);

  useEffect(() => {
    setLoadPercent(null);
    setLoadError(null);
  }, [state?.url]);

  const levels = useMemo(() => {
    const total = state?.total ?? 0;
    const offered = LEVELS.filter((l) => total === 0 || l < total);
    return [...offered, 0];
  }, [state?.total]);

  const stageRef = useRef<HTMLDivElement>(null);
  const [fullscreen, setFullscreen] = useState(false);
  // The inline height below wins over the fullscreen UA stylesheet, so the
  // element has to be told to drop it.
  useEffect(() => {
    const onChange = () => setFullscreen(document.fullscreenElement === stageRef.current);
    document.addEventListener('fullscreenchange', onChange);
    return () => document.removeEventListener('fullscreenchange', onChange);
  }, []);

  const toggleFullscreen = () => {
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => undefined);
    } else {
      stageRef.current?.requestFullscreen().catch(() => undefined);
    }
  };

  const cameraPoses = cameras?.available ? cameras.cameras : null;
  const isSplat = state?.kind === 'splat';
  // Hooks cannot be conditional, so the tool is always constructed and only
  // *enabled* over a splat — `useCrop` fetches nothing when it is not.
  const cropEnabled = withCrop && isSplat;
  const crop = useCrop(projectId, cropEnabled, String(refreshKey ?? ''));
  const isMesh = state?.kind === 'mesh';

  // The saved viewpoint (§7.6d). Same shape as the crop above it: always
  // constructed, because hooks cannot be conditional, and only *enabled* over
  // the splat this app has something to do with a camera for.
  const viewpointEnabled = withViewpoint && isSplat;
  const viewpointApi = useRef<ViewpointApi | null>(null);
  const viewpoint = useViewpoint(projectId, viewpointEnabled);
  // A restore that has to wait for the canvas to come back: restoring a view
  // saved under the other vertical turns the scene over first, and that
  // remounts the splat canvas (its scene rotation is read once, at load).
  const [pendingView, setPendingView] = useState<Viewpoint | null>(null);

  const saveView = () => {
    const captured = viewpointApi.current?.capture();
    if (captured) void viewpoint.save(captured);
  };

  const goToSavedView = () => {
    const saved = viewpoint.viewpoint;
    if (!saved) return;
    if (saved.flip_up !== upFlipped) {
      setPendingView(saved);
      setUpFlipped(saved.flip_up);
    } else {
      viewpointApi.current?.restore(saved);
    }
  };

  const building = Boolean(state?.building);
  const canvasKey = `${state?.url ?? 'none'}#${viewNonce}`;

  // The Reconstruction Region editor used to live here, and it held the parsed
  // preview so it could count the points inside the box. Both are deleted, not
  // ported: every part of that feature existed to work around RealityScan
  // (CLAUDE.md §12, 2026-08-27), and the masking it fed is `spirula sam` now
  // (§7.4). `PointCloudCanvas` still offers `onPositions` for whatever wants
  // the loaded points next; nothing here asks for them.

  if (!state || (!state.available && !error)) {
    return (
      <div
        className="flex flex-col items-center justify-center gap-2 text-slate-500 rounded-lg
                   bg-slate-900/60 border border-slate-700"
        style={{ height }}
      >
        <Boxes className="w-7 h-7" />
        <span className="text-sm">
          {state ? 'Nothing to preview yet — run the step first.' : 'Looking for a result…'}
        </span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        {/* No levels for a mesh: there is nothing decimated to open instead. */}
        {!isMesh && (
        <div className="flex items-center gap-1 rounded-md bg-slate-800 border border-slate-700 p-0.5">
          {levels.map((value) => (
            <button
              key={value}
              onClick={() => setLevel(value)}
              className={`px-2 py-1 text-xs rounded transition-colors ${
                effectiveLevel === value
                  ? 'bg-cyan-600 text-white'
                  : 'text-slate-400 hover:text-slate-100'
              }`}
              title={value === 0
                ? 'Every point in the file'
                : `Up to ${formatCount(value)} points, spread over the whole file`}
            >
              {value === 0 ? 'Full' : formatCount(value)}
            </button>
          ))}
        </div>
        )}

        {isMesh && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setWireframe((v) => !v)}
            className={`gap-1 text-xs ${wireframe ? 'text-cyan-400' : 'text-slate-500'}`}
            title="Draw the triangles. A surface that looks solid and a surface that is solid are the same picture until you see the edges"
          >
            <Grid3x3 className="w-3.5 h-3.5" />
            Wireframe
          </Button>
        )}

        {cameraPoses && (
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setShowCameras((v) => !v)}
              className={`gap-1 text-xs ${showCameras ? 'text-cyan-400' : 'text-slate-500'}`}
            >
              <Camera className="w-3.5 h-3.5" />
              {cameras?.count} cameras
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setShowPath((v) => !v)}
              disabled={!showCameras}
              className={`gap-1 text-xs ${showPath && showCameras ? 'text-cyan-400' : 'text-slate-500'}`}
            >
              <Route className="w-3.5 h-3.5" />
              Path
            </Button>
          </>
        )}

        {!isSplat && !isMesh && (
          <label className="flex items-center gap-2 text-xs text-slate-500">
            Point size
            <input
              type="range"
              min={0.5}
              max={5}
              step={0.1}
              value={pointSize}
              onChange={(e) => setPointSize(Number(e.target.value))}
              className="w-20 accent-cyan-500"
            />
          </label>
        )}

        {viewpointEnabled && (
          <div className="flex items-center gap-1 rounded-md bg-slate-800 border border-slate-700 p-0.5">
            <button
              onClick={saveView}
              disabled={!state?.ready || viewpoint.saving}
              className="px-2 py-1 text-xs rounded transition-colors inline-flex items-center
                         gap-1 text-slate-400 hover:text-slate-100 disabled:opacity-40"
              title="Store the camera as it is now. The export writes it into the PLY header, or beside the file in a .viewpoint.json for the formats that cannot carry it"
            >
              <BookmarkPlus className="w-3.5 h-3.5" />
              {viewpoint.saving ? 'Saving…' : 'Save view'}
            </button>
            {viewpoint.viewpoint && (
              <>
                <button
                  onClick={goToSavedView}
                  className="px-2 py-1 text-xs rounded transition-colors inline-flex items-center
                             gap-1 text-cyan-400 hover:text-cyan-300"
                  title={`Go back to the saved view — ${describeViewpoint(viewpoint.viewpoint)}`}
                >
                  <Bookmark className="w-3.5 h-3.5" />
                  Saved
                </button>
                <button
                  onClick={() => void viewpoint.clear()}
                  disabled={viewpoint.saving}
                  className="px-1.5 py-1 text-xs rounded text-slate-500 hover:text-red-400
                             transition-colors disabled:opacity-40"
                  title="Forget the saved view. The next export carries no camera"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </>
            )}
          </div>
        )}

        <div className="flex-1" />

        <Button
          variant="ghost"
          size="sm"
          onClick={() => setUpFlipped((v) => !v)}
          className={`gap-1 text-xs ${upFlipped ? 'text-cyan-400' : 'text-slate-400 hover:text-slate-100'}`}
          title="Turn the scene over — the mapper levels the model on the cameras, and that is the wrong vertical on a capture with nothing level in it"
        >
          <FlipVertical2 className="w-3.5 h-3.5" />
          Flip up
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setViewNonce((n) => n + 1)}
          className="text-slate-400 hover:text-slate-100 gap-1 text-xs"
          title="Reframe the scene"
        >
          <RotateCcw className="w-3.5 h-3.5" />
          Reset view
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={toggleFullscreen}
          className="text-slate-400 hover:text-slate-100 gap-1 text-xs"
          title="Fullscreen — the wizard column is too narrow to judge a scene in"
        >
          <Maximize2 className="w-3.5 h-3.5" />
          Fullscreen
        </Button>
        {state.source_url && (
          <a href={staticUrl(state.source_url)} download={state.source_file}>
            <Button variant="ghost" size="sm" className="text-slate-400 hover:text-slate-100 gap-1 text-xs">
              <Download className="w-3.5 h-3.5" />
              Source
            </Button>
          </a>
        )}
      </div>

      {/* Canvas */}
      <div
        ref={stageRef}
        className="relative rounded-lg overflow-hidden border border-slate-700 bg-slate-950
                   [&:fullscreen]:rounded-none [&:fullscreen]:border-0"
        style={{ height: fullscreen ? '100%' : height }}
      >
        {state.ready && state.url && isMesh && (
          <MeshCanvas
            key={canvasKey}
            url={state.url}
            background={viewerDefaults?.background ?? '#0b1220'}
            cameras={cameraPoses}
            showCameras={showCameras}
            showPath={showPath}
            flipUp={upFlipped}
            fovX={cameras?.fov_x}
            aspect={cameras?.aspect}
            wireframe={wireframe}
            onProgress={(loaded, total) =>
              setLoadPercent(total ? (loaded / total) * 100 : null)}
            onLoaded={() => setLoadPercent(null)}
            onError={setLoadError}
          />
        )}
        {state.ready && state.url && !isSplat && !isMesh && (
          <PointCloudCanvas
            key={canvasKey}
            url={state.url}
            pointSize={pointSize}
            background={viewerDefaults?.background ?? '#0b1220'}
            cameras={cameraPoses}
            showCameras={showCameras}
            showPath={showPath}
            flipUp={upFlipped}
            fovX={cameras?.fov_x}
            aspect={cameras?.aspect}
            onProgress={(loaded, total) =>
              setLoadPercent(total ? (loaded / total) * 100 : null)}
            onLoaded={() => setLoadPercent(null)}
            onError={setLoadError}
          />
        )}
        {state.ready && state.url && isSplat && (
          <SplatCanvas
            // The scene rotation is read once, when the splat is added, so a
            // flip has to remount the canvas — unlike the point cloud.
            key={`${canvasKey}#${upFlipped}`}
            url={state.url}
            background={viewerDefaults?.background ?? '#0b1220'}
            cameras={cameraPoses}
            showCameras={showCameras}
            showPath={showPath}
            flipUp={upFlipped}
            fovX={cameras?.fov_x}
            aspect={cameras?.aspect}
            crop={cropEnabled ? {
              volumes: crop.volumes,
              selectedId: crop.selectedId,
              gizmoMode: crop.gizmoMode,
              showVolumes: crop.showVolumes,
              livePreview: crop.livePreview,
              onChange: crop.update,
              onSelect: crop.select,
              onLiveSupport: crop.setLiveSupported,
            } : null}
            onBounds={crop.setBounds}
            viewpointApi={viewpointApi}
            restoreOnLoad={pendingView}
            onRestored={() => setPendingView(null)}
            onProgress={(percent) => setLoadPercent(percent)}
            onLoaded={() => setLoadPercent(null)}
            onError={setLoadError}
          />
        )}

        {(building || !state.ready) && !error && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-slate-950/80">
            <svg className="w-7 h-7 animate-spin text-cyan-500" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
            </svg>
            <div className="text-center">
              <p className="text-sm text-slate-300">
                Building the preview{state.total ? ` — ${state.total.toLocaleString()} points` : ''}
              </p>
              <p className="text-xs text-slate-500">
                {state.source_file}
                {state.source_bytes ? ` · ${formatBytes(state.source_bytes)}` : ''}
              </p>
            </div>
            {typeof state.progress === 'number' && (
              <div className="w-48 h-1 rounded bg-slate-800 overflow-hidden">
                <div
                  className="h-full bg-cyan-500 transition-all"
                  style={{ width: `${Math.round(state.progress * 100)}%` }}
                />
              </div>
            )}
          </div>
        )}

        {loadPercent !== null && state.ready && (
          <div className="absolute bottom-0 left-0 right-0 h-1 bg-slate-800">
            <div className="h-full bg-cyan-500 transition-all" style={{ width: `${loadPercent}%` }} />
          </div>
        )}
      </div>

      {cropEnabled && (
        <CropPanel
          volumes={crop.volumes}
          selectedId={crop.selectedId}
          gizmoMode={crop.gizmoMode}
          showVolumes={crop.showVolumes}
          livePreview={crop.livePreview}
          liveSupported={crop.liveSupported}
          state={crop.state}
          dirty={crop.dirty}
          running={crop.running}
          error={crop.error}
          onSelect={crop.select}
          onAdd={(kind, mode) => crop.add(kind, mode, upFlipped)}
          onUpdate={crop.update}
          onRemove={crop.remove}
          onClear={crop.clear}
          onGizmoMode={crop.setGizmoMode}
          onShowVolumes={crop.setShowVolumes}
          onLivePreview={crop.setLivePreview}
          onApply={crop.apply}
        />
      )}

      {/* Footer */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
        <span className="text-slate-400">
          {state.kind === 'splat' ? 'Gaussian splat'
            : state.kind === 'mesh' ? 'Textured mesh'
            : 'Point cloud'}
        </span>
        {/* A mesh reports its vertex count off `mesh_result.json` and can
            legitimately report 0 — an older run predates the field. */}
        {isMesh && (
          <span>
            {state.total ? `${state.total.toLocaleString()} vertices` : 'vertex count unknown'}
            {state.bytes ? ` · ${formatBytes(state.bytes)}` : ''}
          </span>
        )}
        {!isMesh && state.count !== undefined && state.total !== undefined && (
          <span>
            {state.count.toLocaleString()}
            {state.decimated && ` of ${state.total.toLocaleString()}`}
            {state.kind === 'splat' ? ' gaussians' : ' points'}
            {state.bytes ? ` · ${formatBytes(state.bytes)}` : ''}
          </span>
        )}
        {state.source_file && <span className="font-mono">{state.source_file}</span>}
        {cameras?.available && cameras.gaps_known && (cameras.missing_count ?? 0) > 0 && (
          <span className="text-amber-400">
            {cameras.missing_count} frame(s) unaligned — the gaps are marked amber
          </span>
        )}
        {state.decimated && (
          <span className="text-slate-600">
            Decimated for display — “Full” loads every point.
          </span>
        )}
        {viewpointEnabled && viewpoint.viewpoint && (
          <span className="text-cyan-500/80" title="Dataset frame — the frame spirula wrote">
            View saved: {describeViewpoint(viewpoint.viewpoint)}
          </span>
        )}
        {viewpointEnabled && viewpoint.error && (
          <span className="text-red-400">{viewpoint.error}</span>
        )}
      </div>

      {(error || loadError || state.error) && (
        <p className="flex items-start gap-2 text-xs text-red-400 bg-red-950/30 border border-red-800 rounded px-3 py-2">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-px" />
          <span className="flex-1">{error ?? loadError ?? state.error}</span>
          <button onClick={refresh} className="text-red-300 hover:text-red-200 inline-flex items-center gap-1">
            <RefreshCw className="w-3 h-3" /> Retry
          </button>
        </p>
      )}
    </div>
  );
};

export default SceneViewer;
