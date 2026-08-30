import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * The neural checkpoints of CLAUDE.md §7.4 and §7.5 — `/api/models`.
 *
 * Start-and-poll, like the viewer previews and for the same reason: the WS bus
 * carries no project id (§13.7) and every consumer of it maps a step name onto
 * the open project's bar, so a 2 GB download would move a bar that has nothing
 * to do with it.
 *
 * The poll is only alive while something is downloading. An idle Checkpoints
 * panel makes one request when it opens and then nothing at all.
 */

export type ModelState =
  | 'missing'
  | 'partial'
  | 'ready'
  | 'damaged'
  | 'downloading'
  | 'verifying';

export interface ModelFileState {
  filename: string;
  path: string;
  state: ModelState;
  bytes: number;
  part_bytes: number;
  expected_bytes: number;
}

export interface DownloadJob {
  model_id: string;
  label: string;
  state: 'downloading' | 'verifying' | 'ready' | 'cancelled' | 'error';
  downloaded: number;
  total: number;
  progress: number;
  elapsed_s: number;
  rate_bps: number | null;
  eta_s: number | null;
  error: string | null;
}

export interface ModelRow {
  id: string;
  family: 'sam' | 'geometry';
  label: string;
  blurb: string;
  filename: string;
  url: string;
  sha256: string | null;
  size_bytes: number;
  licence: string;
  extras: { filename: string; url: string; size_bytes: number }[];
  recommended: boolean;
  total_bytes: number;
  state: ModelState;
  files: ModelFileState[];
  installed_bytes: number;
  path: string;
  job: DownloadJob | null;
}

export interface LicenceRow {
  id: string;
  name: string;
  url: string;
  summary: string;
  audited: boolean;
}

export interface ModelsOverview {
  cache_dir: string;
  cache_dir_exists: boolean;
  spirula_default_cache: string;
  is_spirula_default: boolean;
  cache_bytes: number;
  disk_free_bytes: number | null;
  licences: Record<string, LicenceRow>;
  models: ModelRow[];
  unmanaged: { filename: string; bytes: number }[];
  download: DownloadJob | null;
}

export interface InUse {
  sam: { value: string; model_id: string | null; label: string | null } | null;
  geometry: { value: string; model_id: string | null; label: string | null } | null;
}

const POLL_MS = 700;

export const useModels = (active: boolean) => {
  const [overview, setOverview] = useState<ModelsOverview | null>(null);
  const [inUse, setInUse] = useState<InUse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [a, b] = await Promise.all([
        fetch('/api/models/'),
        fetch('/api/models/in-use'),
      ]);
      if (a.ok) setOverview(await a.json());
      if (b.ok) setInUse(await b.json());
    } catch (e) {
      setError(String(e));
    }
  }, []);

  // Poll only while a download is live. `overview.download` is null the moment
  // the job leaves the downloading/verifying states, so the loop stops itself
  // one tick after the file lands.
  useEffect(() => {
    if (!active) return;
    void refresh();
  }, [active, refresh]);

  useEffect(() => {
    if (!active || !overview?.download) {
      if (timer.current) window.clearTimeout(timer.current);
      timer.current = null;
      return;
    }
    timer.current = window.setTimeout(() => void refresh(), POLL_MS);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [active, overview?.download, refresh]);

  const call = useCallback(
    async (path: string, init?: RequestInit) => {
      setError(null);
      setBusy(path);
      try {
        const response = await fetch(path, {
          headers: { 'Content-Type': 'application/json' },
          ...init,
        });
        if (!response.ok) {
          const body = await response.json().catch(() => ({}));
          setError(body.detail ?? `${response.status} ${response.statusText}`);
          return null;
        }
        return await response.json();
      } catch (e) {
        setError(String(e));
        return null;
      } finally {
        setBusy(null);
        await refresh();
      }
    },
    [refresh],
  );

  return {
    overview,
    inUse,
    error,
    busy,
    refresh,
    clearError: () => setError(null),
    download: (id: string, licence: string) =>
      call(`/api/models/${id}/download`, {
        method: 'POST',
        body: JSON.stringify({ accept_licence: licence }),
      }),
    cancel: (id: string) => call(`/api/models/${id}/cancel`, { method: 'POST' }),
    verify: (id: string) => call(`/api/models/${id}/verify`, { method: 'POST' }),
    adopt: (id: string, path: string) =>
      call(`/api/models/${id}/adopt`, {
        method: 'POST',
        body: JSON.stringify({ path }),
      }),
    remove: (id: string) => call(`/api/models/${id}`, { method: 'DELETE' }),
    use: (id: string) => call(`/api/models/${id}/use`, { method: 'POST' }),
  };
};
