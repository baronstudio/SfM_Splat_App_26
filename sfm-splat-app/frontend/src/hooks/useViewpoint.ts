import { useCallback, useEffect, useState } from 'react';
import client from '@/api/client';
import { usePipelineStore } from '@/store/pipelineStore';
import { fromStored, type Viewpoint } from '@/components/viewer/viewpoint';
import type { Project } from '@/types';

/**
 * The saved viewpoint of a project (CLAUDE.md §7.6d).
 *
 * Layer 3 of §4's settings model, under `viewpoint`, and — like the crop
 * volumes and unlike everything else there — with **no `defaults.json`
 * counterpart**: a camera parked in front of *this* scene is not a default any
 * other project could inherit. It lives in `settings_json` rather than under
 * `train/` for the crop's reason too, which is that it must outlive a step 4
 * reset: the frame comes from the sparse model, so a re-train leaves a saved
 * view exactly as valid as it was.
 *
 * There is no debounce here and there is no Save button anywhere else in this
 * app — both are right. Every other panel saves a *stream* of edits, so it
 * debounces and never asks; this saves one deliberate act, "the view I want
 * shipped with the file", which is a click by definition.
 */

export interface ViewpointTool {
  viewpoint: Viewpoint | null;
  saving: boolean;
  savedAt: number | null;
  error: string | null;
  save(view: Viewpoint): Promise<void>;
  clear(): Promise<void>;
}

function readViewpoint(settingsJson: string | null | undefined): Viewpoint | null {
  try {
    const parsed = JSON.parse(settingsJson || '{}');
    return fromStored(parsed?.viewpoint);
  } catch {
    return null;
  }
}

export function useViewpoint(
  projectId: string | null, enabled: boolean,
): ViewpointTool {
  const upsertProject = usePipelineStore((s) => s.upsertProject);

  const [viewpoint, setViewpoint] = useState<Viewpoint | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setViewpoint(null);
    setSavedAt(null);
    setError(null);
    if (!projectId || !enabled) return undefined;

    let cancelled = false;
    client
      .get<Project>(`/projects/${projectId}`)
      .then((res) => {
        if (!cancelled) setViewpoint(readViewpoint(res.data.settings_json));
      })
      .catch(() => {
        // A viewpoint nobody can read is a missing viewpoint, not a broken
        // viewer: the button offers to save one over it.
        if (!cancelled) setViewpoint(null);
      });
    return () => { cancelled = true; };
  }, [projectId, enabled]);

  const write = useCallback(async (value: Viewpoint | null) => {
    if (!projectId) return;
    setSaving(true);
    try {
      const res = await client.patch<Project>(`/projects/${projectId}`, {
        settings: { viewpoint: value },
      });
      upsertProject(res.data);
      // Read it back out of what the server stored rather than trusting what
      // was sent: `null` clears the key through the same deep merge every other
      // section is patched with, and this is what proves it did.
      setViewpoint(readViewpoint(res.data.settings_json));
      setSavedAt(Date.now());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save the viewpoint');
    } finally {
      setSaving(false);
    }
  }, [projectId, upsertProject]);

  return {
    viewpoint,
    saving,
    savedAt,
    error,
    save: (view) => write(view),
    clear: () => write(null),
  };
}

export default useViewpoint;
