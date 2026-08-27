import React, { useEffect, useState } from 'react';
import { Settings, CheckCircle, Layers, RefreshCw, Sliders } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { usePipelineStore } from '@/store/pipelineStore';
import { usePipeline } from '@/hooks/usePipeline';
import { useSettings } from '@/hooks/useSettings';
import { useDefaults } from '@/hooks/useDefaults';
import { useCuration } from '@/hooks/useCuration';
import { useSources } from '@/hooks/useSources';
import { useProjectSettings } from '@/hooks/useProjectSettings';
import { ProgressBar } from '@/components/panels/ProgressBar';
import { FrameGallery } from '@/components/panels/FrameGallery';
import { SharpnessTimeline } from '@/components/panels/SharpnessTimeline';
import { SourcePanel } from '@/components/panels/SourcePanel';
import FFmpegSettings from '@/components/settings/FFmpegSettings';
import CurateSettings from '@/components/settings/CurateSettings';
import SaveState from '@/components/settings/SaveState';
import client from '@/api/client';
import type { CurateDefaults, ExtractDefaults, SelectionSummary, SourceProbe } from '@/types';

/** One stat of the curation summary. */
const Stat: React.FC<{ label: string; value: string; tone?: string; hint?: string }> = ({
  label, value, tone = 'text-slate-100', hint,
}) => (
  <div className="flex flex-col" title={hint}>
    <span className={`text-lg font-semibold ${tone}`}>{value}</span>
    <span className="text-[11px] text-slate-500 uppercase tracking-wide">{label}</span>
  </div>
);

const CurationSummary: React.FC<{ summary: SelectionSummary; inBandRatio?: number }> = ({
  summary, inBandRatio,
}) => (
  <div className="rounded-lg bg-slate-800 border border-slate-700 px-4 py-3">
    <div className="grid grid-cols-2 sm:grid-cols-5 gap-4">
      <Stat label="kept" value={`${summary.kept}/${summary.total}`} tone="text-green-400" />
      <Stat
        label="removed"
        value={`${summary.removed_pct.toFixed(1)}%`}
        tone="text-red-400"
        hint={`${summary.rejected_blur} blur, ${summary.rejected_redundant} redundant, ${summary.rejected_manual} manual`}
      />
      <Stat label="blur" value={String(summary.rejected_blur)} tone="text-red-300" />
      <Stat label="redundant" value={String(summary.rejected_redundant)} tone="text-purple-300" />
      <Stat
        label="gaps"
        value={String(summary.warning_gap)}
        tone={summary.warning_gap > 0 ? 'text-amber-400' : 'text-slate-400'}
        hint="Frames whose step exceeds the band — the likely alignment breaks."
      />
    </div>
    {inBandRatio !== undefined && (
      <p className="text-xs text-slate-500 mt-2">
        {(inBandRatio * 100).toFixed(0)}% of consecutive kept pairs sit inside the overlap band.
      </p>
    )}
  </div>
);


/**
 * The one question an image set asks that a video never does.
 *
 * Not a modal: a modal at the click of "Conform" is answered on reflex, and the
 * answer has to be visible next to the estimate it changes. Not a checkbox
 * buried in the Extraction panel either — it decides the frame format and
 * whether LichtFeld Studio trains on the background at all, which is more than
 * a checkbox's worth of consequence.
 *
 * What it does *not* decide is anything about RealityScan: RS has no alpha
 * concept for source images and aligns on the full frame either way. The
 * channel is cargo — carried inside the images to the COLMAP export, and
 * extracted to `masks/` as a second copy in case RS drops it.
 */
const AlphaChoice: React.FC<{
  set: NonNullable<ReturnType<typeof useSources>['primarySet']>;
  keepAlpha: boolean;
  onChange: (keep: boolean) => void;
  disabled?: boolean;
}> = ({ set, keepAlpha, onChange, disabled }) => {
  const unused = set.alpha_in_use === false;
  return (
    <div className="rounded-lg border border-emerald-800/60 bg-emerald-950/10 px-4 py-3">
      <p className="text-sm font-medium text-emerald-300">
        These PNGs carry an alpha channel
        {unused && (
          <span className="font-normal text-slate-400">
            {' '}— though the sampled images are fully opaque, so it may be the
            renderer's default rather than a cut-out
          </span>
        )}
        .
      </p>
      <div className="mt-2 flex flex-col gap-2">
        {[
          {
            keep: true,
            title: 'Keep it — LichtFeld Studio can train through it',
            body: 'Frames stay RGBA PNG, and the channel is also extracted to '
              + 'masks/ as one image per frame. RealityScan aligns on the full '
              + 'image either way; step 4 trains with --mask-mode from whichever '
              + 'copy reaches the COLMAP dataset, and step 3 says which did.',
          },
          {
            keep: false,
            title: 'Drop it',
            body: 'Frames are written as JPEG, like a video extraction. Lighter, '
              + 'and the background is trained along with everything else.',
          },
        ].map((option) => (
          <label
            key={String(option.keep)}
            className={`flex cursor-pointer gap-2.5 rounded-md border px-3 py-2 transition-colors ${
              keepAlpha === option.keep
                ? 'border-emerald-600 bg-emerald-900/20'
                : 'border-slate-700 bg-slate-800/40 hover:border-slate-600'
            } ${disabled ? 'opacity-60 cursor-not-allowed' : ''}`}
          >
            <input
              type="radio"
              name="keep-alpha"
              className="mt-1 accent-emerald-500"
              checked={keepAlpha === option.keep}
              disabled={disabled}
              onChange={() => onChange(option.keep)}
            />
            <span className="min-w-0">
              <span className="block text-sm text-slate-100">{option.title}</span>
              <span className="block text-xs text-slate-400">{option.body}</span>
            </span>
          </label>
        ))}
      </div>
    </div>
  );
};

const Step2_Extract: React.FC = () => {
  const { currentProjectId, stepStatuses, setCurrentStep } = usePipelineStore();
  const { startPipeline } = usePipeline();
  const { settings } = useSettings();
  const { defaults, presets, previewFps } = useDefaults();
  const {
    frames, summary, analysis, analysed, loading,
    reanalyse, setOverride, refresh, clear, error: curationError,
  } = useCuration(currentProjectId);
  const {
    sources, imageSets, primary: primarySource, primarySet, sourceKind,
    ffmpegAvailable,
    loading: sourcesLoading, error: sourcesError, refresh: refreshSources,
  } = useSources(currentProjectId);
  // An imported image set outranks a video in the same `input/` — the backend
  // decides that (`resolve_input_source`) and the step only reports it, so the
  // wording on screen cannot disagree with what step 2 actually reads.
  const usingImages = sourceKind === 'images';

  const [showSettings, setShowSettings] = useState(false);
  const [showCurate, setShowCurate] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [probe, setProbe] = useState<SourceProbe | null>(null);
  const [fpsExplanation, setFpsExplanation] = useState<string>('');
  const [workingFps, setWorkingFps] = useState<number | null>(null);

  // Layer 3 of the settings model (CLAUDE.md §4): the app defaults with this
  // project's overrides on top, written back on every change so re-opening the
  // step shows what the user set rather than defaults.json.
  const {
    value: extract, setValue: setExtract, flush: flushExtract,
    saving: savingExtract, savedAt: savedExtractAt, error: extractSaveError,
  } = useProjectSettings<ExtractDefaults>(currentProjectId, 'extract', defaults?.extract ?? null);
  const {
    value: curate, setValue: setCurate, flush: flushCurate,
    saving: savingCurate, savedAt: savedCurateAt, error: curateSaveError,
  } = useProjectSettings<CurateDefaults>(currentProjectId, 'curate', defaults?.curate ?? null);

  const status = stepStatuses[2];  // step 2 = extract + curate
  const isRunning = status === 'running';
  const isDone = status === 'done';

  // The source metadata only exists after a first extraction — absent is fine.
  useEffect(() => {
    if (!currentProjectId) return;
    client
      .get(`/files/${currentProjectId}/probe`)
      .then((r) => setProbe(r.data?.probe ?? null))
      .catch(() => setProbe(null));
  }, [currentProjectId, isDone]);

  // The metadata the policy resolves against. The live probe of the file step 2
  // is about to open wins over `analysis/probe.json`, which describes the video
  // of the *last* run — and does not exist at all before the first one, which is
  // exactly when these settings are being chosen.
  const sourceProbe = primarySource?.probe ?? probe;

  // Resolve the policy against this project's real source when we know it.
  useEffect(() => {
    if (!extract) return;
    if (usingImages) {
      // Every image is a frame: there is no cadence to resolve, and the
      // resolver would answer about a video step 2 is not going to open.
      setFpsExplanation('');
      setWorkingFps(null);
      return;
    }
    const t = setTimeout(() => {
      previewFps(extract, sourceProbe?.fps ?? null, sourceProbe?.duration_s ?? null)
        .then((r) => { setFpsExplanation(r.explanation); setWorkingFps(r.fps); })
        .catch(() => { setFpsExplanation(''); setWorkingFps(null); });
    }, 250);
    return () => clearTimeout(t);
  }, [extract, sourceProbe, previewFps, usingImages]);

  /** Everything the backend needs to resolve both phases of step 2. */
  const jobSettings = () => ({ extract: extract ?? {}, curate: curate ?? {} });

  /** Land any debounced edit before the run reads the project row. */
  const saveSettings = () => Promise.all([flushExtract(), flushCurate()]);

  const handleExtract = async () => {
    if (!currentProjectId || !extract) return;
    setError(null);
    // The step wipes frames/ and analysis/ before FFmpeg runs, so the gallery
    // and the curation stats go with them — showing the previous set while the
    // new one extracts is showing frames that are already deleted.
    clear();
    try {
      await saveSettings();
      await startPipeline(currentProjectId, 2, jobSettings());
      // Cheap (both halves are cached on the file fingerprint) and it re-reads
      // input/ in case the video was replaced from another screen since mount.
      refreshSources();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to start extraction');
    }
  };

  const handleReanalyse = async () => {
    setError(null);
    try {
      await saveSettings();
      await reanalyse(jobSettings());
    } catch {
      /* surfaced through curationError */
    }
  };

  const handleDelete = async (filenames: string[]) => {
    if (!currentProjectId || filenames.length === 0) return;
    try {
      await client.delete(`/files/${currentProjectId}/frames`, { data: { filenames } });
      refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to delete frames');
    }
  };

  const fpsSummary = extract
    ? extract.fps_mode === 'auto'
      ? `auto → ${extract.target_frame_count} frames`
      : extract.fps_mode === 'ratio'
        ? `ratio ${extract.fps_ratio}`
        : `${extract.fps_absolute} fps`
    : '—';

  const activePreset = presets.find((p) => p.id === extract?.capture_preset);
  const hasFrames = frames.length > 0;

  return (
    <div className="flex flex-col gap-6 p-6 max-w-4xl mx-auto">
      <h2 className="text-xl font-semibold text-slate-100">
        Step 2 — Frame Extraction &amp; Curation
      </h2>

      {/* What is in input/, and what step 2 will do with it */}
      <SourcePanel
        sources={sources}
        imageSets={imageSets}
        sourceKind={sourceKind}
        keepAlpha={extract?.keep_alpha}
        loading={sourcesLoading}
        error={sourcesError}
        ffmpegAvailable={ffmpegAvailable}
        workingFps={workingFps}
        maxFrames={extract?.max_frames}
        scalePercent={extract?.scale_percent}
        quality={extract?.quality}
        onRefresh={refreshSources}
      />

      {usingImages && primarySet?.has_alpha && extract && (
        <AlphaChoice
          set={primarySet}
          keepAlpha={extract.keep_alpha}
          disabled={isRunning}
          onChange={(keep) => setExtract({ ...extract, keep_alpha: keep })}
        />
      )}

      {/* Settings summary */}
      <div className="flex items-center justify-between rounded-lg bg-slate-800 border border-slate-700 px-4 py-3 flex-wrap gap-2">
        <div className="flex gap-4 text-sm text-slate-300 flex-wrap">
          {usingImages ? (
            <>
              <span className="flex items-center gap-1.5">
                <Layers className="w-3.5 h-3.5 text-violet-400" />
                Source:{' '}
                <span className="text-slate-100 font-medium">
                  image set · {primarySet?.image_count ?? 0} images
                </span>
              </span>
              <span>
                Alpha:{' '}
                <span className={primarySet?.has_alpha && extract?.keep_alpha
                  ? 'text-emerald-400 font-medium' : 'text-slate-500 font-medium'}>
                  {!primarySet?.has_alpha ? 'none' : extract?.keep_alpha ? 'kept in frames' : 'dropped'}
                </span>
              </span>
            </>
          ) : (
            <>
              <span>FPS: <span className="text-slate-100 font-medium">{fpsSummary}</span></span>
              <span>Dedup: <span className="text-slate-100 font-medium">{extract?.mpdecimate ? 'on' : 'off'}</span></span>
            </>
          )}
          <span>Quality: <span className="text-slate-100 font-medium">{extract?.quality ?? '—'}</span></span>
          <span>Scale: <span className="text-slate-100 font-medium">{extract?.scale_percent ?? 100}%</span></span>
          <span>
            Curation:{' '}
            <span className={curate?.enabled ? 'text-cyan-400 font-medium' : 'text-slate-500 font-medium'}>
              {curate?.enabled ? 'on' : 'off'}
            </span>
          </span>
          {settings?.tools?.ffmpeg_path && (
            <span className="text-slate-500 truncate max-w-xs">ffmpeg: {settings.tools.ffmpeg_path}</span>
          )}
        </div>
        <div className="flex gap-1 items-center">
          <SaveState
            saving={savingExtract || savingCurate}
            savedAt={Math.max(savedExtractAt ?? 0, savedCurateAt ?? 0) || null}
            error={extractSaveError ?? curateSaveError}
          />
          <Button
            variant="ghost" size="sm"
            onClick={() => { setShowSettings((v) => !v); setShowCurate(false); }}
            className="text-slate-400 hover:text-slate-100 gap-1"
          >
            <Settings className="w-4 h-4" />
            Extraction
          </Button>
          <Button
            variant="ghost" size="sm"
            onClick={() => { setShowCurate((v) => !v); setShowSettings(false); }}
            className="text-slate-400 hover:text-slate-100 gap-1"
          >
            <Sliders className="w-4 h-4" />
            Curation
          </Button>
        </div>
      </div>

      {fpsExplanation && !showSettings && !usingImages && (
        <p className="text-xs text-cyan-400 font-mono -mt-3">{fpsExplanation}</p>
      )}

      {showSettings && extract && (
        <div className="rounded-lg bg-slate-800 border border-slate-700 p-4">
          <FFmpegSettings
            settings={extract}
            presets={presets}
            onChange={setExtract}
            fpsExplanation={fpsExplanation}
            sourceSize={sourceProbe}
          />
        </div>
      )}

      {showCurate && curate && (
        <div className="rounded-lg bg-slate-800 border border-slate-700 p-4">
          <CurateSettings settings={curate} preset={activePreset} onChange={setCurate} />
        </div>
      )}

      {(error || curationError) && (
        <p className="text-sm text-red-400 bg-red-950/30 border border-red-800 rounded px-3 py-2">
          {error ?? curationError}
        </p>
      )}

      {/* Actions */}
      <div className="flex gap-2 flex-wrap">
        <Button
          onClick={handleExtract}
          disabled={isRunning || !currentProjectId || !extract}
          className="bg-cyan-600 hover:bg-cyan-500 text-white"
        >
          {isRunning
            ? 'Working…'
            : usingImages
              ? hasFrames ? 'Re-conform Images' : 'Conform Images'
              : hasFrames ? 'Re-extract Frames' : 'Extract Frames'}
        </Button>
        {/* Re-analysing never re-extracts: thresholds are tuned iteratively (§6.3). */}
        <Button
          onClick={handleReanalyse}
          disabled={isRunning || !hasFrames || !currentProjectId}
          variant="ghost"
          className="border border-slate-700 text-slate-300 hover:text-slate-100 gap-1"
          title="Re-run the curation on the frames already on disk"
        >
          <RefreshCw className="w-4 h-4" />
          Re-analyse
        </Button>
      </div>

      {(isRunning || isDone) && (
        <div className="flex flex-col gap-2">
          <ProgressBar
            step="extract"
            label={usingImages ? '1. Conforming images' : '1. Frame extraction'}
          />
          {curate?.enabled && <ProgressBar step="curate" label="2. Curation" />}
        </div>
      )}

      {/* Curation results */}
      {analysed && summary && (
        <CurationSummary
          summary={summary}
          inBandRatio={analysis?.scores?.stats.overlap.in_band_ratio}
        />
      )}

      {analysis?.scores && analysis.scores.frames.length > 0 && (
        <SharpnessTimeline scores={analysis.scores} />
      )}

      {analysis?.scores && (
        <p className="text-xs text-slate-500 -mt-3">
          {analysis.scores.sequences.length} sequence(s) · {analysis.scores.params.scene_method} ·{' '}
          {analysis.scores.params.band_source}
        </p>
      )}

      {currentProjectId && (hasFrames || isRunning) && (
        <FrameGallery
          frames={frames}
          loading={loading}
          analysed={analysed}
          summary={summary}
          onOverride={setOverride}
          onDelete={handleDelete}
        />
      )}

      {isDone && (
        <div className="flex items-center justify-between">
          <span className="text-sm text-green-400 font-medium">
            {analysed && summary
              ? `${summary.kept} frames selected for alignment`
              : 'Extraction complete'}
          </span>
          <Button
            onClick={() => setCurrentStep(3)}
            className="bg-green-700 hover:bg-green-600 text-white gap-1"
          >
            <CheckCircle className="w-4 h-4" />
            Validate &amp; Continue to RS Alignment
          </Button>
        </div>
      )}
    </div>
  );
};

export default Step2_Extract;
