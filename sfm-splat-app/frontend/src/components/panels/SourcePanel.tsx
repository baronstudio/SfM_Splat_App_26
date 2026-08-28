import React, { useState } from 'react';
import {
  AlertTriangle, Film, FileText, Images, Layers, Loader2, PlayCircle, RefreshCw,
} from 'lucide-react';
import { staticUrl } from '@/api/client';
import type { ImageSet, InputSourceKind, SourceFile } from '@/types';
import { VideoPlayerDialog } from './VideoPlayerDialog';
import {
  formatBitrate, formatBytes, formatCount, formatDuration, resolutionClass,
  sourceFrameCount,
} from './sourceFormat';

interface SourcePanelProps {
  sources: SourceFile[];
  /** Imported sets of pre-extracted frames (§6.7). */
  imageSets?: ImageSet[];
  /** Which of the two step 2 will actually read. */
  sourceKind?: InputSourceKind;
  /** `extract.keep_alpha` — what the conform will do with a PNG alpha channel. */
  keepAlpha?: boolean;
  loading?: boolean;
  error?: string | null;
  ffmpegAvailable?: boolean;
  /** The fps the current policy resolves to, so the estimate is the real one. */
  workingFps?: number | null;
  /** `extract.max_frames` — 0 means uncapped. */
  maxFrames?: number;
  /** `extract.scale_percent` — 100 means the frames keep the source size. */
  scalePercent?: number;
  /** JPEG quality (`-qscale:v`), shown only when the conform will re-encode. */
  quality?: number;
  onRefresh?: () => void;
}

const Badge: React.FC<{ tone: string; children: React.ReactNode; title?: string }> = ({
  tone, children, title,
}) => (
  <span
    title={title}
    className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${tone}`}
  >
    {children}
  </span>
);

const Field: React.FC<{ label: string; value: string; tone?: string }> = ({
  label, value, tone = 'text-slate-200',
}) => (
  <div className="flex flex-col min-w-0">
    <span className="text-[10px] uppercase tracking-wide text-slate-500">{label}</span>
    <span className={`text-xs font-medium truncate ${tone}`}>{value}</span>
  </div>
);

/** What this source will actually produce under the settings on screen. */
const ExtractionEstimate: React.FC<{
  source: SourceFile;
  workingFps?: number | null;
  maxFrames?: number;
  scalePercent?: number;
}> = ({ source, workingFps, maxFrames = 0, scalePercent = 100 }) => {
  const p = source.probe;
  if (!p?.duration_s || !workingFps) return null;

  const raw = Math.floor(p.duration_s * workingFps);
  const capped = maxFrames > 0 && maxFrames < raw;
  const count = capped ? maxFrames : raw;

  // Both sides truncated to an even number, as the extraction does (§6.1) —
  // an estimate that disagrees with the frames on disk is worse than none.
  const even = (n: number) => Math.trunc((n * scalePercent) / 100 / 2) * 2;
  const scaled =
    scalePercent < 100 && p.width && p.height
      ? `${even(p.width)}×${even(p.height)}`
      : null;

  // Zero is not a small number here, it is a failed run: FFmpeg's image2 output
  // exits non-zero with `Nothing was written into output file` when no frame
  // reaches it, and the backend now refuses the extraction outright. Say so
  // where the setting is, not in the error afterwards.
  if (count < 1) {
    return (
      <p className="text-xs text-amber-400 mt-2">
        ⚠ {workingFps} fps over {p.duration_s.toFixed(1)}s of source is{' '}
        <span className="font-semibold tabular-nums">less than one frame</span> — the
        extraction will be refused. Raise the frames per second to at least{' '}
        <span className="font-semibold tabular-nums">
          {(1 / p.duration_s).toPrecision(3)}
        </span>
        , or switch the fps mode to auto.
      </p>
    );
  }

  return (
    <p className="text-xs text-cyan-400/90 mt-2">
      ≈ <span className="font-semibold tabular-nums">{formatCount(count)}</span> frames at{' '}
      {workingFps.toFixed(2)} fps
      {capped && <span className="text-amber-400"> (capped from {formatCount(raw)})</span>}
      {scaled && <span className="text-slate-400"> · written at {scaled}</span>}
      <span className="text-slate-500"> — this is what curation will score.</span>
    </p>
  );
};


/** An imported set of frames, and what step 2 will do to it.
 *
 * The questions here are not the video ones. There is no cadence to choose and
 * no duration to sample: every image is a frame, so what matters is how many
 * there are, whether they agree with each other (one resolution, one format),
 * how heavy each one is, and whether they carry an alpha channel — which is the
 * only decision this panel actually asks for.
 */
const ImageSetRow: React.FC<{
  set: ImageSet;
  isPrimary: boolean;
  keepAlpha?: boolean;
  maxFrames?: number;
  scalePercent?: number;
  quality?: number;
}> = ({ set, isPrimary, keepAlpha = true, maxFrames = 0, scalePercent = 100, quality }) => {
  const [posterBroken, setPosterBroken] = useState(false);
  const formats = Object.entries(set.formats);
  const mixedFormat = formats.length > 1;
  const alpha = set.has_alpha && keepAlpha;
  const capped = maxFrames > 0 && maxFrames < set.image_count;
  const kept = capped ? maxFrames : set.image_count;
  // The conform copies rather than re-encodes when nothing has to change — same
  // rule as `plan_output` in step_conform, stated where the settings are set.
  const outSuffix = alpha ? '.png' : '.jpg';
  const passthrough =
    scalePercent === 100 && !mixedFormat && formats[0]?.[0] === outSuffix;
  const even = (n: number) => Math.trunc((n * scalePercent) / 100 / 2) * 2;

  return (
    <li
      className={`flex gap-4 rounded-lg border px-3 py-3 ${
        isPrimary ? 'border-violet-800/70 bg-violet-950/10' : 'border-slate-700 bg-slate-800/40'
      }`}
    >
      <div className="relative w-40 shrink-0 overflow-hidden rounded border border-slate-700
                      bg-slate-900 aspect-video">
        {set.thumb_url && !posterBroken ? (
          <img
            src={staticUrl(set.thumb_url)}
            alt={set.name}
            onError={() => setPosterBroken(true)}
            className="h-full w-full object-cover"
            loading="lazy"
          />
        ) : (
          <span className="flex h-full w-full items-center justify-center">
            <Images className="w-6 h-6 text-slate-600" />
          </span>
        )}
        <span className="absolute bottom-1 right-1 rounded bg-black/75 px-1 text-[10px]
                         tabular-nums text-slate-200">
          {formatCount(set.image_count)} img
        </span>
      </div>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-slate-100 truncate">{set.name}</span>
          {isPrimary && (
            <Badge
              tone="bg-violet-900/60 text-violet-300"
              title="Step 2 conforms this set into frames/ — there is nothing to extract."
            >
              image set
            </Badge>
          )}
          {!isPrimary && (
            <Badge tone="bg-slate-700 text-slate-300" title="Step 2 reads one source per project.">
              unused by step 2
            </Badge>
          )}
          {set.origin === 'zip' && (
            <Badge tone="bg-slate-700 text-slate-300" title={set.origin_name}>
              from zip
            </Badge>
          )}
          {set.has_alpha && (
            <Badge
              tone={set.alpha_in_use === false
                ? 'bg-slate-700 text-slate-400'
                : 'bg-emerald-900/60 text-emerald-300'}
              title={
                set.alpha_in_use === false
                  ? 'The images declare an alpha channel but the sampled ones are fully opaque.'
                  : 'PNG alpha channel — carried into the frames, and on to LichtFeld Studio as a training mask.'
              }
            >
              {set.alpha_in_use === false ? 'alpha (unused)' : 'alpha'}
            </Badge>
          )}
          {!set.uniform_size && (
            <Badge
              tone="bg-amber-900/60 text-amber-300"
              title="The sampled images do not share one resolution."
            >
              mixed sizes
            </Badge>
          )}
        </div>

        <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
          <Field label="images" value={formatCount(set.image_count)} />
          <Field
            label="resolution"
            value={set.width && set.height ? `${set.width}×${set.height}` : '—'}
          />
          <Field
            label="format"
            value={formats.map(([ext, n]) => `${ext.slice(1)}${mixedFormat ? ` ×${n}` : ''}`).join(' + ') || '—'}
            tone={mixedFormat ? 'text-amber-300' : 'text-slate-200'}
          />
          <Field label="total size" value={formatBytes(set.total_bytes)} />
          <Field label="avg / image" value={formatBytes(set.avg_bytes)} />
          <Field
            label={`at ${set.nominal_fps} img/s`}
            value={formatDuration(set.duration_s)}
          />
          <Field label="naming" value={`${set.pattern}${formats[0]?.[0] ?? ''}`} />
          <Field label="original naming" value={set.original_pattern || '—'} />
          <Field
            label="imported from"
            value={set.origin === 'folder' ? (set.origin_path || 'folder')
              : set.origin === 'zip' ? (set.origin_name || 'zip')
              : 'uploaded files'}
          />
        </div>

        {isPrimary && (
          <p className="text-xs text-violet-300/90 mt-2">
            ≈ <span className="font-semibold tabular-nums">{formatCount(kept)}</span>{' '}
            frames written as <span className="font-mono">{outSuffix}</span>
            {capped && (
              <span className="text-amber-400"> (capped from {formatCount(set.image_count)})</span>
            )}
            {scalePercent < 100 && set.width && set.height && (
              <span className="text-slate-400">
                {' '}· resized to {even(set.width)}×{even(set.height)}
              </span>
            )}
            {passthrough ? (
              <span className="text-slate-500">
                {' '}— nothing to re-encode, the frames are linked as they are.
              </span>
            ) : (
              <span className="text-slate-500">
                {' '}— re-encoded{!alpha && quality ? ` at -qscale:v ${quality}` : ''}.
              </span>
            )}
            {alpha && (
              <span className="text-emerald-400/90">
                {' '}Alpha kept in the frames, and extracted to{' '}
                <span className="font-mono">masks/</span> as one image per frame.
              </span>
            )}
            {set.has_alpha && !keepAlpha && (
              <span className="text-slate-500"> Alpha dropped.</span>
            )}
          </p>
        )}
      </div>
    </li>
  );
};

const VideoRow: React.FC<{
  source: SourceFile;
  multipleVideos: boolean;
  onPlay: () => void;
  workingFps?: number | null;
  maxFrames?: number;
  scalePercent?: number;
}> = ({ source, multipleVideos, onPlay, workingFps, maxFrames, scalePercent }) => {
  // The poster lives in `preview/`, which a reset from step 3 on wipes (§14.1).
  // A URL that 404s must fall back to the placeholder, not to a broken image.
  const [posterBroken, setPosterBroken] = useState(false);
  const p = source.probe;
  const total = sourceFrameCount(p);
  const resClass = resolutionClass(p?.width ?? null, p?.height ?? null);
  const isPrimary = source.is_extraction_source;

  return (
    <li
      className={`flex gap-4 rounded-lg border px-3 py-3 ${
        isPrimary ? 'border-cyan-800/70 bg-cyan-950/10' : 'border-slate-700 bg-slate-800/40'
      }`}
    >
      {/* Poster — the click target for the mini player */}
      <button
        type="button"
        onClick={onPlay}
        title="Play this video"
        className="group relative w-40 shrink-0 overflow-hidden rounded border border-slate-700
                   bg-slate-900 aspect-video hover:border-cyan-500 transition-colors"
      >
        {source.thumb_url && !posterBroken ? (
          <img
            src={staticUrl(source.thumb_url)}
            alt={source.filename}
            onError={() => setPosterBroken(true)}
            className="h-full w-full object-cover"
            loading="lazy"
          />
        ) : (
          <span className="flex h-full w-full items-center justify-center">
            <Film className="w-6 h-6 text-slate-600" />
          </span>
        )}
        <span className="absolute inset-0 flex items-center justify-center bg-black/30 opacity-0
                         group-hover:opacity-100 transition-opacity">
          <PlayCircle className="w-9 h-9 text-white drop-shadow" />
        </span>
        {p?.duration_s != null && (
          <span className="absolute bottom-1 right-1 rounded bg-black/75 px-1 text-[10px]
                           tabular-nums text-slate-200">
            {formatDuration(p.duration_s)}
          </span>
        )}
      </button>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-slate-100 truncate">{source.filename}</span>
          {isPrimary && (
            <Badge
              tone="bg-cyan-900/60 text-cyan-300"
              title="Step 2 extracts from the first .mp4, else the first .mov — this one."
            >
              extraction source
            </Badge>
          )}
          {!isPrimary && multipleVideos && (
            <Badge
              tone="bg-slate-700 text-slate-300"
              title="Step 2 reads one video per project. This file is not extracted."
            >
              unused by step 2
            </Badge>
          )}
          {resClass && <Badge tone="bg-slate-700 text-slate-300">{resClass}</Badge>}
          {p?.hdr && (
            <Badge
              tone="bg-amber-900/60 text-amber-300"
              title="HDR transfer — the extracted JPEGs are SDR, so expect a flatter, darker frame set."
            >
              hdr
            </Badge>
          )}
        </div>

        {source.probe_error ? (
          <p className="mt-2 flex items-start gap-1.5 text-xs text-red-400">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-px" />
            ffprobe could not read this file: {source.probe_error}
          </p>
        ) : (
          <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
            <Field
              label="resolution"
              value={p?.width && p?.height ? `${p.width}×${p.height}` : '—'}
            />
            <Field label="codec" value={p?.codec ?? '—'} />
            <Field
              label="source fps"
              value={p?.fps != null ? `${p.fps}` : '—'}
              tone={p?.fps && p.fps >= 90 ? 'text-cyan-300' : 'text-slate-200'}
            />
            <Field
              label="duration"
              value={p?.duration_s != null ? formatDuration(p.duration_s) : '—'}
            />
            <Field
              label={total?.exact === false ? 'total frames (est.)' : 'total frames'}
              value={total ? `${total.exact ? '' : '≈ '}${formatCount(total.count)}` : '—'}
            />
            <Field label="bitrate" value={p?.bitrate ? formatBitrate(p.bitrate) : '—'} />
            <Field label="size" value={formatBytes(source.size_bytes)} />
            <Field label="container" value={p?.container?.split(',')[0] ?? '—'} />
            <Field label="pixel format" value={p?.pix_fmt ?? '—'} />
          </div>
        )}

        {isPrimary && (
          <ExtractionEstimate
            source={source}
            workingFps={workingFps}
            maxFrames={maxFrames}
            scalePercent={scalePercent}
          />
        )}
      </div>
    </li>
  );
};

/**
 * What the project holds in `input/`, and what step 2 is about to do with it.
 *
 * Step 2 used to say nothing at all about its own input: the settings summary
 * named the fps policy and the ffmpeg path, but the video those apply to was a
 * filename on the previous screen. Choosing between `auto`, `ratio 0.2` and an
 * absolute fps is a decision about the source cadence (§6.2), and downscaling
 * is a decision about its resolution (§6.1) — neither was on screen where it is
 * made. The poster frames are also the cheapest answer to "is this the right
 * rush", which one click turns into the mini player.
 */
export const SourcePanel: React.FC<SourcePanelProps> = ({
  sources,
  imageSets = [],
  sourceKind = 'video',
  keepAlpha,
  loading = false,
  error = null,
  ffmpegAvailable = true,
  workingFps,
  maxFrames,
  scalePercent,
  quality,
  onRefresh,
}) => {
  const [playing, setPlaying] = useState<SourceFile | null>(null);

  const videos = sources.filter((s) => s.kind === 'video');
  const others = sources.filter((s) => s.kind !== 'video');
  const usingSets = sourceKind === 'images';
  const primarySetName =
    imageSets.find((s) => s.is_extraction_source)?.name ?? imageSets[0]?.name;

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800/60 px-4 py-3">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="text-xs font-medium uppercase tracking-wide text-slate-400">
          Input sources
          {(videos.length > 0 || imageSets.length > 0) && (
            <span className="ml-2 text-slate-500 normal-case tracking-normal">
              {imageSets.length > 0 && (
                <>
                  {imageSets.length} image set{imageSets.length > 1 ? 's' : ''}
                  {videos.length > 0 && ' · '}
                </>
              )}
              {videos.length > 0 && `${videos.length} video${videos.length > 1 ? 's' : ''}`}
              {others.length > 0 && ` · ${others.length} other file${others.length > 1 ? 's' : ''}`}
            </span>
          )}
        </h3>
        <div className="flex items-center gap-2">
          {loading && <Loader2 className="w-3.5 h-3.5 animate-spin text-slate-500" />}
          {onRefresh && (
            <button
              type="button"
              onClick={onRefresh}
              title="Re-read the input directory"
              className="text-slate-500 hover:text-slate-200 transition-colors"
            >
              <RefreshCw className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>

      {error && (
        <p className="mb-2 text-xs text-red-400">{error}</p>
      )}

      {imageSets.length > 0 && (
        <ul className="mb-2 flex flex-col gap-2">
          {imageSets.map((set) => (
            <ImageSetRow
              key={set.name}
              set={set}
              isPrimary={usingSets && set.name === primarySetName}
              keepAlpha={keepAlpha}
              maxFrames={maxFrames}
              scalePercent={scalePercent}
              quality={quality}
            />
          ))}
        </ul>
      )}

      {usingSets && videos.length > 0 && (
        <p className="mb-2 flex items-start gap-1.5 text-xs text-amber-400">
          <Layers className="w-3.5 h-3.5 shrink-0 mt-px" />
          This project holds an image set <em>and</em> a video. Step 2 reads the
          image set; the video below is not touched. Delete the set from step 1
          to go back to extracting from the video.
        </p>
      )}

      {videos.length === 0 && imageSets.length === 0 && !loading ? (
        <p className="text-sm text-slate-500">
          Nothing in this project's <span className="font-mono">input/</span> — step 1 is
          where a video is uploaded or a set of images is imported.
        </p>
      ) : videos.length === 0 ? null : (
        <ul className="flex flex-col gap-2">
          {videos.map((s) => (
            <VideoRow
              key={s.filename}
              source={s}
              multipleVideos={videos.length > 1}
              onPlay={() => setPlaying(s)}
              workingFps={workingFps}
              maxFrames={maxFrames}
              scalePercent={scalePercent}
            />
          ))}
        </ul>
      )}

      {videos.length > 1 && !usingSets && (
        <p className="mt-2 flex items-start gap-1.5 text-xs text-amber-400">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-px" />
          Step 2 extracts from one video per project. The others sit in{' '}
          <span className="font-mono">input/</span> untouched — split them into their own
          projects, or delete them from step 1.
        </p>
      )}

      {others.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
          {others.map((s) => (
            <li key={s.filename} className="flex items-center gap-1.5 text-xs text-slate-500">
              <FileText className="w-3.5 h-3.5 shrink-0" />
              <span className="truncate">{s.filename}</span>
              <span className="text-slate-600">{formatBytes(s.size_bytes)}</span>
            </li>
          ))}
        </ul>
      )}

      {!ffmpegAvailable && videos.length > 0 && (
        <p className="mt-2 text-xs text-slate-500">
          No ffmpeg found, so no poster frames — set <span className="font-mono">ffmpeg_path</span>{' '}
          in Settings → Tools. The videos still play.
        </p>
      )}

      <VideoPlayerDialog source={playing} onClose={() => setPlaying(null)} />
    </div>
  );
};

export default SourcePanel;
