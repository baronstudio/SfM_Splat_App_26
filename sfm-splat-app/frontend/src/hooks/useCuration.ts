import { useCallback, useEffect, useRef, useState } from 'react';
import client from '../api/client';
import { usePipelineStore } from '../store/pipelineStore';
import type {
  AnalysisResponse,
  ExtractDefaults,
  FrameInfo,
  FramesResponse,
  Override,
  SelectionSummary,
} from '../types';

/**
 * Frames + curation verdicts for one project (CLAUDE.md §6.3).
 *
 * Owns the polling so the gallery and the timeline never fetch the same thing
 * twice, and exposes the two write paths that differ enormously in cost:
 *
 *   reanalyse()   re-runs the whole analysis — seconds to minutes, WS-tracked.
 *   setOverride() rewrites overrides.json and re-derives the selection —
 *                 instant, no image is re-read.
 */
export const useCuration = (projectId: string | null) => {
  const stepStatuses = usePipelineStore((s) => s.stepStatuses);
  const step2Running = stepStatuses[2] === 'running';

  const [frames, setFrames] = useState<FrameInfo[]>([]);
  const [summary, setSummary] = useState<SelectionSummary | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisResponse | null>(null);
  const [analysed, setAnalysed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = useCallback(async () => {
    if (!projectId) return;
    try {
      const [f, a] = await Promise.all([
        client.get<FramesResponse>(`/files/${projectId}/frames`),
        client.get<AnalysisResponse>(`/files/${projectId}/analysis`),
      ]);
      setFrames(f.data.frames ?? []);
      setSummary(f.data.summary ?? null);
      setAnalysed(Boolean(f.data.analysed));
      setAnalysis(a.data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load frames');
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    setLoading(true);
    refresh();
  }, [refresh]);

  // Poll while step 2 runs so the gallery fills in during extraction and the
  // verdicts land as soon as the analysis writes selection.json.
  useEffect(() => {
    if (step2Running && projectId) {
      timer.current = setInterval(refresh, 2000);
    } else if (timer.current) {
      clearInterval(timer.current);
      timer.current = null;
      // One last read: the final write lands just after the step reports done.
      refresh();
    }
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [step2Running, projectId, refresh]);

  /**
   * Drop everything the previous run produced, without waiting for the server.
   *
   * A re-extraction deletes `frames/` and `analysis/` before FFmpeg writes the
   * first frame (`step_extract._clear_previous_run`), so the gallery and the
   * stats on screen describe a frame set that no longer exists. They are
   * emptied on the click rather than on the first poll, which is up to two
   * seconds later.
   */
  const clear = useCallback(() => {
    setFrames([]);
    setSummary(null);
    setAnalysis(null);
    setAnalysed(false);
    setError(null);
  }, []);

  /** Re-run the curation on the frames already on disk. */
  const reanalyse = useCallback(
    async (settings: Partial<ExtractDefaults> & Record<string, unknown> = {}) => {
      if (!projectId) return;
      setError(null);
      try {
        await client.post('/pipeline/analyze', { project_id: projectId, settings });
      } catch (e) {
        const msg = e instanceof Error ? e.message : 'Failed to start the analysis';
        setError(msg);
        throw e;
      }
    },
    [projectId],
  );

  /** Force a frame kept or dropped. `null` hands it back to the automatic verdict. */
  const setOverride = useCallback(
    async (filename: string, verdict: Override | null) => {
      if (!projectId) return;
      // Optimistic: the round-trip rewrites two small JSON files, but the grid
      // should not wait on it to show the click landed.
      setFrames((prev) =>
        prev.map((f) =>
          f.filename === filename
            ? {
                ...f,
                override: verdict,
                verdict: verdict === 'keep' ? 'kept' : verdict === 'drop' ? 'rejected' : f.verdict,
                reason: verdict ? 'manual' : f.reason,
              }
            : f,
        ),
      );
      try {
        await client.patch(`/projects/${projectId}`, { overrides: { [filename]: verdict } });
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to save the override');
      }
      refresh();
    },
    [projectId, refresh],
  );

  return {
    frames, summary, analysis, analysed, loading, error,
    refresh, reanalyse, setOverride, clear,
  };
};

export default useCuration;
