import { useCallback, useEffect, useState } from 'react';
import client from '@/api/client';
import type { ImageSet, InputSourceKind, SourceFile, SourcesResponse } from '@/types';

/**
 * What sits in the project's `input/` directory, probed.
 *
 * Distinct from the `/probe` route step 2 already read: that one returns
 * `analysis/probe.json`, the metadata of the video the *last* extraction ran
 * on, and it does not exist until there has been one. This is what is on disk
 * now — which is what the next run will read.
 *
 * Two kinds of source live there since §6.7: videos, and imported image sets.
 * `sourceKind` is the backend's answer to which one step 2 will consume — the
 * same `resolve_input_source` the extraction itself calls, so the badge cannot
 * drift from the file (or the folder) FFmpeg opens.
 */
export const useSources = (projectId: string | null) => {
  const [sources, setSources] = useState<SourceFile[]>([]);
  const [imageSets, setImageSets] = useState<ImageSet[]>([]);
  const [sourceKind, setSourceKind] = useState<InputSourceKind>('none');
  const [sourceName, setSourceName] = useState<string | null>(null);
  const [extractionSource, setExtractionSource] = useState<string | null>(null);
  const [ffmpegAvailable, setFfmpegAvailable] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!projectId) {
      setSources([]);
      setImageSets([]);
      setSourceKind('none');
      setSourceName(null);
      setExtractionSource(null);
      return;
    }
    setLoading(true);
    try {
      const res = await client.get<SourcesResponse>(`/files/${projectId}/sources`);
      setSources(res.data.sources);
      setImageSets(res.data.image_sets ?? []);
      setSourceKind(res.data.source_kind ?? 'none');
      setSourceName(res.data.source_name ?? null);
      setExtractionSource(res.data.extraction_source);
      setFfmpegAvailable(res.data.ffmpeg_available);
      setError(null);
    } catch (e) {
      setSources([]);
      setImageSets([]);
      setError(e instanceof Error ? e.message : 'Failed to read the source files');
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  /** The one video step 2 would extract from (first .mp4, else first .mov). It
   *  stays populated even when an image set outranks it, because step 2 warns
   *  about the video it is *not* reading. */
  const primary = sources.find((s) => s.is_extraction_source) ?? null;

  /** The image set step 2 will conform, or null when the source is a video. */
  const primarySet =
    imageSets.find((s) => s.is_extraction_source) ??
    (sourceKind === 'images' ? imageSets[0] ?? null : null);

  return {
    sources,
    imageSets,
    primary,
    primarySet,
    sourceKind,
    sourceName,
    extractionSource,
    ffmpegAvailable,
    loading,
    error,
    refresh,
  };
};
