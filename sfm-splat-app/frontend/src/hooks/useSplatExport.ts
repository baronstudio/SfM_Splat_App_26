import { useCallback, useEffect, useMemo, useState } from 'react';
import client from '@/api/client';
import { usePipeline } from '@/hooks/usePipeline';
import { useDefaults } from '@/hooks/useDefaults';
import { useProjectSettings } from '@/hooks/useProjectSettings';
import type {
  SplatExportDefaults, SplatExportFormat, SplatExportState,
} from '@/types';

/**
 * The splat export pass (CLAUDE.md §7.6c): the settings, the drawer of files it
 * has already written, and the one call that adds another.
 *
 * ── Why this is not `useCrop` ──────────────────────────────────────────────
 *
 * The crop stores its volumes in `settings_json` and nowhere else, because a
 * box placed around *this* scene is not a default anything could inherit. An
 * export plan is the opposite: "SH 0, .sog" is exactly the sort of thing that
 * should follow you from project to project, so it is an ordinary §4 section —
 * `defaults.json` under `export`, overridden per project — and it rides
 * `useProjectSettings`'s 300 ms debounced PATCH of a diff like every other
 * panel in the app.
 *
 * The other difference is what the pass produces. A crop is pipeline data and
 * can go **stale**, which is most of `useCrop`'s surface: the volumes move, the
 * file on disk stops describing them, and steps 5 and 6 read it anyway. An
 * export is terminal — no step reads `train/export/` — so there is nothing to
 * be stale against. Change the settings and the old file is simply an older
 * deliverable that is still exactly what it says it is.
 */

/** The five formats, in the order the panel offers them. */
export const EXPORT_FORMATS: {
  value: SplatExportFormat;
  label: string;
  hint: string;
  external?: boolean;
}[] = [
  {
    value: 'ply',
    label: 'PLY',
    hint: 'What the trainer wrote. Every tool reads it; nothing is quantised.',
  },
  {
    value: 'splat',
    label: '.splat',
    hint: '32 bytes per gaussian, no spherical harmonics — 7.75x smaller, and '
      + 'what every web viewer opens.',
  },
  {
    value: 'sog',
    label: 'SOG',
    hint: 'PlayCanvas\'s web format: WebP textures plus meta.json, bundled. '
      + 'Measured 20.6x smaller.',
    external: true,
  },
  {
    value: 'spz',
    label: 'SPZ',
    hint: 'Niantic\'s compressed format. Measured 16.5x smaller, and the '
      + 'fastest of the three to write.',
    external: true,
  },
  {
    value: 'compressed-ply',
    label: 'Compressed PLY',
    hint: 'A PLY that stays a PLY — chunked and quantised. Measured 4.0x '
      + 'smaller, and SuperSplat opens it directly.',
    external: true,
  },
];

export interface SplatExportTool {
  settings: SplatExportDefaults | null;
  setSettings(next: SplatExportDefaults): void;
  state: SplatExportState | null;
  saving: boolean;
  savedAt: number | null;
  saveError: string | null;
  running: boolean;
  error: string | null;
  /** Whether the chosen format can be written at all right now. */
  blocked: boolean;
  run(): Promise<void>;
  clear(): Promise<void>;
  refresh(): void;
}

export function useSplatExport(
  projectId: string | null,
  enabled: boolean,
  /**
   * Wizard step 4's status. The export pass attaches to that step and hands its
   * previous status back when it finishes (`_run_attached_pass`), so a run is
   * `running` and then whatever it was before — which is both the signal that
   * the pass is over and the cue to re-read the drawer.
   */
  stepStatus: string | undefined,
): SplatExportTool {
  const { defaults } = useDefaults();
  const { runSplatExport } = usePipeline();
  const {
    value: settings, setValue: setSettings, saving, savedAt,
    error: saveError, flush,
  } = useProjectSettings<SplatExportDefaults>(
    projectId, 'export',
    (defaults?.export as SplatExportDefaults | undefined) ?? null,
  );

  const [state, setState] = useState<SplatExportState | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (!projectId || !enabled) {
      setState(null);
      return undefined;
    }
    let cancelled = false;
    client
      .get<SplatExportState>(`/files/${projectId}/export-splat`)
      .then((res) => {
        if (cancelled) return;
        setState(res.data);
        setError(null);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setState(null);
        setError(e instanceof Error ? e.message : 'Failed to read the export state');
      });
    return () => { cancelled = true; };
  }, [projectId, enabled, nonce, stepStatus]);

  // A run finished: `_run_attached_pass` restored step 4's own status, so the
  // drawer has a new file in it and nothing else said so.
  useEffect(() => {
    if (stepStatus !== 'running') setRunning(false);
  }, [stepStatus]);

  const blocked = useMemo(() => {
    if (!settings || !state) return false;
    const external = state.formats.external.includes(settings.format);
    return external && !state.splat_transform.available;
  }, [settings, state]);

  const run = useCallback(async () => {
    if (!projectId || !settings) return;
    setError(null);
    setRunning(true);
    try {
      // The panel has no Save button (§4), so the pending diff has to land
      // before the pass reads `settings_json` on the server.
      await flush();
      await runSplatExport(projectId, { export: settings });
    } catch (e: unknown) {
      setRunning(false);
      setError(e instanceof Error ? e.message : 'Failed to start the export');
    }
  }, [projectId, settings, flush, runSplatExport]);

  const clear = useCallback(async () => {
    if (!projectId) return;
    setError(null);
    try {
      await client.delete(`/files/${projectId}/export-splat`);
      setNonce((n) => n + 1);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to clear the exports');
    }
  }, [projectId]);

  return {
    settings, setSettings, state, saving, savedAt, saveError,
    running, error, blocked, run, clear,
    refresh: () => setNonce((n) => n + 1),
  };
}

export default useSplatExport;
