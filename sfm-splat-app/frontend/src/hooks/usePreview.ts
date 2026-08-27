import { useCallback, useEffect, useState } from 'react';
import client from '@/api/client';
import type { CamerasReport, PreviewSource, PreviewState } from '@/types';

/**
 * Drives one viewer preview: ask what exists, start the build if it does not,
 * poll until it does.
 *
 * The build is a POST that returns immediately rather than a request held open
 * for the length of the conversion — five million gaussians take seconds, and a
 * hung request is indistinguishable from a hung app.
 */
export function usePreview(
  projectId: string | null,
  source: PreviewSource,
  maxCount: number,
  enabled = true,
) {
  const [state, setState] = useState<PreviewState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  /** Re-check the source — call it when a step finishes writing one. */
  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (!projectId || !enabled) {
      setState(null);
      return undefined;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const path = `/files/${projectId}/preview`;
    const params = { source, max_count: maxCount };

    const poll = async () => {
      try {
        const res = await client.get<PreviewState>(path, { params });
        if (cancelled) return;
        setState(res.data);
        setError(res.data.error ?? null);

        if (!res.data.available || res.data.ready) return;
        if (res.data.error && !res.data.building) return;
        if (!res.data.building) {
          await client.post<PreviewState>(path, null, { params });
          if (cancelled) return;
        }
        timer = setTimeout(poll, 500);
      } catch (err: unknown) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : 'Preview request failed');
      }
    };

    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [projectId, source, maxCount, enabled, nonce]);

  return { state, error, refresh };
}

/** Camera poses of the last alignment — the same set for every step. */
export function useCameras(projectId: string | null, enabled = true) {
  const [cameras, setCameras] = useState<CamerasReport | null>(null);
  const [nonce, setNonce] = useState(0);
  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (!projectId || !enabled) {
      setCameras(null);
      return undefined;
    }
    let cancelled = false;
    client
      .get<CamerasReport>(`/files/${projectId}/cameras`)
      .then((res) => {
        if (!cancelled) setCameras(res.data);
      })
      .catch(() => {
        if (!cancelled) setCameras(null);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, enabled, nonce]);

  return { cameras, refresh };
}
