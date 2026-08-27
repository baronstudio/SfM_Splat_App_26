import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import client from '@/api/client';
import { usePipelineStore } from '@/store/pipelineStore';
import type { Project } from '@/types';

type Dict = Record<string, unknown>;

const isDict = (v: unknown): v is Dict =>
  typeof v === 'object' && v !== null && !Array.isArray(v);

/** Merge `patch` into `base`. Patch wins, base keys survive — mirrors `_deep_merge` on the backend. */
export function deepMerge<T extends Dict>(base: T, patch: Dict): T {
  const out: Dict = { ...base };
  for (const [key, value] of Object.entries(patch)) {
    const before = out[key];
    out[key] = isDict(value) && isDict(before) ? deepMerge(before, value) : value;
  }
  return out as T;
}

/**
 * The keys of `next` that differ from `prev`, nested blocks included.
 *
 * This is what keeps CLAUDE.md §4's promise: a project stores only the keys it
 * really overrides, so changing a default keeps propagating to it. Sending the
 * whole panel back would freeze a full copy of defaults.json into every project.
 */
export function deepDiff(prev: Dict, next: Dict): Dict {
  const out: Dict = {};
  for (const [key, value] of Object.entries(next)) {
    const before = prev[key];
    if (isDict(value) && isDict(before)) {
      const inner = deepDiff(before, value);
      if (Object.keys(inner).length > 0) out[key] = inner;
    } else if (JSON.stringify(before) !== JSON.stringify(value)) {
      out[key] = value;
    }
  }
  return out;
}

/** The stored overrides of one section, out of the project's `settings_json` blob. */
function readSection(settingsJson: string | undefined, section: string): Dict {
  try {
    const all: unknown = JSON.parse(settingsJson || '{}');
    if (!isDict(all)) return {};
    const block = all[section];
    return isDict(block) ? block : {};
  } catch {
    return {};
  }
}

// A slider drag fires an onChange per pixel; coalescing those into one PATCH is
// the only reason a change is not sent on the spot. Short enough that a click
// or a step launch cannot outrun it, and a pending patch is flushed on unmount,
// on a project switch and on `beforeunload` anyway.
const SAVE_DEBOUNCE_MS = 300;

export interface ProjectSettings<T> {
  /** The defaults with this project's overrides applied — null until both have loaded. */
  value: T | null;
  /** Same shape as the settings panels' `onChange`: hand the whole object back. */
  setValue: (next: T) => void;
  /** True once the project's stored overrides have been read. */
  loaded: boolean;
  saving: boolean;
  /** `Date.now()` of the last successful write, for the "saved" hint. */
  savedAt: number | null;
  error: string | null;
  /** Send whatever is still pending, now. Awaited before a step starts. */
  flush: () => Promise<void>;
}

/**
 * One section of `Project.settings_json`, edited in place and saved on every change.
 *
 * Layer 3 of the settings model (CLAUDE.md §4): the panel shows
 * `defaults[section]` with this project's overrides on top, and every edit is
 * PATCHed back as a *diff*, so re-opening the step shows what the user set
 * rather than the app defaults.
 */
export function useProjectSettings<T extends object>(
  projectId: string | null,
  section: string,
  base: T | null,
): ProjectSettings<T> {
  const upsertProject = usePipelineStore((s) => s.upsertProject);

  const [overrides, setOverrides] = useState<Dict | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const pending = useRef<Dict>({});
  // The project the pending patch belongs to: switching project mid-debounce
  // must not write the previous project's edits onto the new one.
  const pendingFor = useRef<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flush = useCallback(async () => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    const patch = pending.current;
    const id = pendingFor.current;
    pending.current = {};
    if (!id || Object.keys(patch).length === 0) return;

    setSaving(true);
    try {
      const res = await client.patch<Project>(`/projects/${id}`, {
        settings: { [section]: patch },
      });
      upsertProject(res.data);
      setSavedAt(Date.now());
      setError(null);
    } catch (e) {
      // Put it back rather than lose it: the next edit — or the next flush —
      // retries it, with anything changed since merged on top.
      pending.current = deepMerge(patch, pending.current);
      pendingFor.current = id;
      setError(e instanceof Error ? e.message : 'Failed to save the project settings');
    } finally {
      setSaving(false);
    }
  }, [section, upsertProject]);

  // Load this project's overrides, and re-read them on every project change so
  // the panels of a freshly selected project never show the previous one's.
  useEffect(() => {
    let cancelled = false;
    setOverrides(null);
    setSavedAt(null);
    setError(null);
    if (!projectId) return;

    client
      .get<Project>(`/projects/${projectId}`)
      .then((res) => {
        if (!cancelled) setOverrides(readSection(res.data.settings_json, section));
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        // Fall back to the bare defaults rather than leave the panel blank — an
        // edit still saves correctly, since only its diff is sent.
        setOverrides({});
        setError(e instanceof Error ? e.message : 'Failed to load the project settings');
      });

    return () => {
      cancelled = true;
    };
  }, [projectId, section]);

  // A step page unmounts the moment the user changes step, and the whole app on
  // a reload. Neither may swallow a patch still sitting in the debounce.
  useEffect(() => {
    const onUnload = () => {
      const patch = pending.current;
      const id = pendingFor.current;
      if (!id || Object.keys(patch).length === 0) return;
      pending.current = {};
      // `keepalive` is what lets this outlive the page; axios has no such
      // option, so this one call goes out through fetch directly.
      fetch(`${client.defaults.baseURL ?? '/api'}/projects/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ settings: { [section]: patch } }),
        keepalive: true,
      }).catch(() => {});
    };
    window.addEventListener('beforeunload', onUnload);
    return () => {
      window.removeEventListener('beforeunload', onUnload);
      void flush();
    };
  }, [flush, section]);

  // The section models are plain interfaces, not index signatures, so the merge
  // and the diff see them as the dictionaries they are on the wire.
  const value = useMemo(
    () => (base && overrides ? (deepMerge(base as unknown as Dict, overrides) as unknown as T) : null),
    [base, overrides],
  );

  const setValue = useCallback(
    (next: T) => {
      if (!value || !projectId) return;
      const patch = deepDiff(value as unknown as Dict, next as unknown as Dict);
      if (Object.keys(patch).length === 0) return;

      // Another project's edits are still queued — send them where they belong
      // before this one's join the queue.
      if (pendingFor.current && pendingFor.current !== projectId) void flush();

      setOverrides((cur) => deepMerge(cur ?? {}, patch));
      pending.current = deepMerge(pending.current, patch);
      pendingFor.current = projectId;

      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => void flush(), SAVE_DEBOUNCE_MS);
    },
    [value, projectId, flush],
  );

  return { value, setValue, loaded: overrides !== null, saving, savedAt, error, flush };
}

export default useProjectSettings;
