import { useCallback, useEffect, useState } from 'react';
import type { AppDefaults, CapturePreset, DefaultsSection, ExtractDefaults } from '@/types';

/**
 * Business defaults per wizard step (defaults.json).
 *
 * Distinct from useSettings, which owns config.json — installation concerns
 * (exe paths). See CLAUDE.md §4 for the three-layer model.
 */
export const useDefaults = () => {
  const [defaults, setDefaults] = useState<AppDefaults | null>(null);
  const [presets, setPresets] = useState<CapturePreset[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchDefaults = useCallback(async () => {
    try {
      const [d, p] = await Promise.all([
        fetch('/api/defaults/'),
        fetch('/api/defaults/presets'),
      ]);
      if (d.ok) setDefaults(await d.json());
      if (p.ok) setPresets(await p.json());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load defaults');
    }
  }, []);

  /** Deep-merged partial update — send only what changed. */
  const updateDefaults = useCallback(async (patch: Partial<AppDefaults>) => {
    setSaving(true);
    try {
      const res = await fetch('/api/defaults/', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setDefaults(await res.json());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save defaults');
    } finally {
      setSaving(false);
    }
  }, []);

  const resetDefaults = useCallback(async (section?: DefaultsSection) => {
    setSaving(true);
    try {
      const url = section ? `/api/defaults/reset?section=${section}` : '/api/defaults/reset';
      const res = await fetch(url, { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setDefaults(await res.json());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to reset defaults');
    } finally {
      setSaving(false);
    }
  }, []);

  /** Ask the backend what a policy resolves to, so the UI never guesses. */
  const previewFps = useCallback(
    async (extract: ExtractDefaults, source_fps: number | null, duration_s: number | null) => {
      const res = await fetch('/api/defaults/fps-preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ extract, source_fps, duration_s }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return (await res.json()) as { fps: number; explanation: string };
    },
    [],
  );

  useEffect(() => {
    fetchDefaults();
  }, [fetchDefaults]);

  return { defaults, presets, saving, error, fetchDefaults, updateDefaults, resetDefaults, previewFps };
};
