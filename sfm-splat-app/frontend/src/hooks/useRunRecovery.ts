import { useEffect, useRef } from 'react';

import client from '../api/client';
import { usePipelineStore } from '../store/pipelineStore';
import type { JobLogEntry, RunJob } from '../types';

/**
 * Find the run that is already going, and put it back on screen.
 *
 * A run is an `asyncio.Task` in the backend process: it is tied to neither the
 * request that started it nor the WebSocket, and nothing in this app aborts
 * anything on `beforeunload` — so leaving the page never stopped the tool.
 * What stopped was the *view*, completely: `pipelineRunning` is state in this
 * store, and so are the bar and the 500-line log. A reload therefore showed a
 * step disabled on `running`, an empty LiveLog, a bar at zero and no Abort
 * button, which is indistinguishable from a job that died — and reading it
 * that way is exactly what happened.
 *
 * `/api/pipeline/status` has answered `job` since P7.1, and this is the caller
 * it was written for. Mounted once by `WizardShell`, it re-checks on every
 * project switch, which is the same trigger `hydrateFromProject` uses.
 *
 * It restores; it never starts, cancels or advances anything.
 */
export const useRunRecovery = (): void => {
  const currentProjectId = usePipelineStore((state) => state.currentProjectId);
  // The last run put back, so a project switched away from and back to does not
  // replay the same log tail on top of the lines the socket has since carried.
  const restoredRef = useRef<string | null>(null);

  useEffect(() => {
    if (!currentProjectId) return;
    let cancelled = false;

    (async () => {
      let job: RunJob | null = null;
      try {
        const { data } = await client.get('/pipeline/status', {
          params: { project_id: currentProjectId },
        });
        job = (data?.job ?? null) as RunJob | null;
      } catch {
        return; // no answer, nothing to restore — the page stays as it is
      }
      if (cancelled || !job || job.state !== 'running') return;
      if (restoredRef.current === job.id) return;
      restoredRef.current = job.id;

      // The log is the nicety; the run is the point. A tail that fails to load
      // still leaves the bar, the step status and the Abort button restored.
      let entries: JobLogEntry[] = [];
      try {
        const { data } = await client.get(`/pipeline/jobs/${job.id}/log`, {
          params: { limit: 500 },
        });
        entries = (data?.entries ?? []) as JobLogEntry[];
      } catch {
        entries = [];
      }
      if (cancelled) return;

      usePipelineStore.getState().hydrateRun(job, entries);
    })();

    return () => {
      cancelled = true;
    };
  }, [currentProjectId]);
};

export default useRunRecovery;
