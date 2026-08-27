import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Boxes, Camera, Download, ExternalLink, FlipVertical2, RefreshCw, RotateCcw, Route,
  Maximize2, AlertTriangle,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { staticUrl } from '@/api/client';
import { useDefaults } from '@/hooks/useDefaults';
import { useSettings } from '@/hooks/useSettings';
import { useCameras, usePreview } from '@/hooks/usePreview';
import { isYDownFrame } from './frame';
import PointCloudCanvas from './PointCloudCanvas';
import SplatCanvas from './SplatCanvas';
import type { PreviewSource } from '@/types';

/**
 * The 3D preview mounted in steps 3, 4 and 5.
 *
 * It picks the renderer from what the file *is*, not from which step asked:
 * a step can produce either a plain sparse cloud or a gaussian PLY, so keying
 * the viewer on the step number would sometimes pick the wrong renderer.
 */

interface SceneViewerProps {
  projectId: string;
  source: PreviewSource;
  /** Re-check the source when this changes — pass the step status. */
  refreshKey?: string | number;
  /** Offer the camera overlay. Off for steps where the poses say nothing new. */
  withCameras?: boolean;
  height?: number;
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
}) => {
  const { defaults } = useDefaults();
  const { settings } = useSettings();
  const viewerDefaults = defaults?.viewer;

  const [level, setLevel] = useState<number | null>(null);
  const [showCameras, setShowCameras] = useState(true);
  const [showPath, setShowPath] = useState(true);
  const [pointSize, setPointSize] = useState(1.6);
  const [loadPercent, setLoadPercent] = useState<number | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [viewNonce, setViewNonce] = useState(0);
  const [upFlipped, setUpFlipped] = useState(false);

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

  const supersplatBase = settings?.tools?.supersplat_url ?? 'https://superspl.at/editor';
  const cameraPoses = cameras?.available ? cameras.cameras : null;
  const isSplat = state?.kind === 'splat';
  const building = Boolean(state?.building);
  const canvasKey = `${state?.url ?? 'none'}#${viewNonce}`;

  // The RC export is Y-down and the LFS output is Y-up (frame.ts), so the fix
  // is per object, not per step. `upFlipped` turns the whole view over on top
  // of that — RC's +Z is only the true vertical when the alignment found it.
  const flipContent = isYDownFrame(source) !== upFlipped;
  const flipCameras = !upFlipped;

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

        {!isSplat && (
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

        <div className="flex-1" />

        <Button
          variant="ghost"
          size="sm"
          onClick={() => setUpFlipped((v) => !v)}
          className={`gap-1 text-xs ${upFlipped ? 'text-cyan-400' : 'text-slate-400 hover:text-slate-100'}`}
          title="Turn the scene over — RealityScan's up axis is only a guess when the alignment had nothing vertical to lean on"
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
        {isSplat && state.source_url && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => window.open(
              `${supersplatBase}?load=${encodeURIComponent(staticUrl(state.source_url as string))}`,
              '_blank', 'noopener,noreferrer',
            )}
            className="text-cyan-400 hover:text-cyan-300 gap-1 text-xs"
            title="SuperSplat must be able to reach this machine to load the file"
          >
            <ExternalLink className="w-3.5 h-3.5" />
            SuperSplat
          </Button>
        )}
      </div>

      {/* Canvas */}
      <div
        ref={stageRef}
        className="relative rounded-lg overflow-hidden border border-slate-700 bg-slate-950
                   [&:fullscreen]:rounded-none [&:fullscreen]:border-0"
        style={{ height: fullscreen ? '100%' : height }}
      >
        {state.ready && state.url && !isSplat && (
          <PointCloudCanvas
            key={canvasKey}
            url={state.url}
            pointSize={pointSize}
            background={viewerDefaults?.background ?? '#0b1220'}
            cameras={cameraPoses}
            showCameras={showCameras}
            showPath={showPath}
            flipCloud={flipContent}
            flipCameras={flipCameras}
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
            key={`${canvasKey}#${flipContent}`}
            url={state.url}
            background={viewerDefaults?.background ?? '#0b1220'}
            cameras={cameraPoses}
            showCameras={showCameras}
            showPath={showPath}
            flipSplat={flipContent}
            flipCameras={flipCameras}
            fovX={cameras?.fov_x}
            aspect={cameras?.aspect}
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

      {/* Footer */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
        <span className="text-slate-400">
          {state.kind === 'splat' ? 'Gaussian splat' : 'Point cloud'}
        </span>
        {state.count !== undefined && state.total !== undefined && (
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
