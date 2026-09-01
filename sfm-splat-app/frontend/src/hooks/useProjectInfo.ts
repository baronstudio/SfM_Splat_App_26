import { useCallback, useEffect, useRef, useState } from 'react';
import client from '@/api/client';
import { usePipelineStore } from '@/store/pipelineStore';
import type { Project } from '@/types';

/** The two free-text fields of a project's info panel. */
export interface ProjectInfoFields {
  footage_author: string;
  description: string;
}

// Same reasoning as useProjectSettings': coalesce a burst of keystrokes into
// one PATCH, short enough that a step launch or a project switch cannot outrun
// it, and flush whatever is pending on unmount and on `beforeunload`.
const SAVE_DEBOUNCE_MS = 400;

const EMPTY: ProjectInfoFields = { footage_author: '', description: '' };

function fieldsOf(project: Project | null | undefined): ProjectInfoFields {
  return {
    footage_author: project?.footage_author ?? '',
    description: project?.description ?? '',
  };
}

/**
 * The project's capture metadata, edited in place and saved on every change.
 *
 * These are columns on the row rather than a `settings_json` section (CLAUDE.md
 * §4): they have no `defaults.json` counterpart to inherit from, and the
 * project list draws the footage author without parsing a blob. There is no
 * Save button here for the same reason there is none on the settings panels —
 * a panel that must be saved is a panel that gets lost.
 */
export function useProjectInfo(project: Project | null | undefined) {
  const upsertProject = usePipelineStore((s) => s.upsertProject);
  const projectId = project?.id ?? null;

  const [fields, setFields] = useState<ProjectInfoFields>(() => fieldsOf(project));
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const pending = useRef<Partial<ProjectInfoFields>>({});
  // Which project the pending patch belongs to: switching project mid-debounce
  // must not write the previous project's text onto the new one.
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
      const res = await client.patch<Project>(`/projects/${id}`, patch);
      upsertProject(res.data);
      setSavedAt(Date.now());
      setError(null);
    } catch (e) {
      // Put it back rather than lose it — anything typed since wins over it.
      pending.current = { ...patch, ...pending.current };
      pendingFor.current = id;
      setError(e instanceof Error ? e.message : 'Failed to save the project info');
    } finally {
      setSaving(false);
    }
  }, [upsertProject]);

  // Re-seed on a project *change* only: reading the store's copy on every
  // render would overwrite what is being typed with what was last saved.
  const seededFor = useRef<string | null>(null);
  useEffect(() => {
    if (seededFor.current === projectId) return;
    if (pendingFor.current && pendingFor.current !== projectId) void flush();
    seededFor.current = projectId;
    setFields(projectId ? fieldsOf(project) : EMPTY);
    setSavedAt(null);
    setError(null);
    // `project` is deliberately not a dependency — see above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, flush]);

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
        body: JSON.stringify(patch),
        keepalive: true,
      }).catch(() => {});
    };
    window.addEventListener('beforeunload', onUnload);
    return () => {
      window.removeEventListener('beforeunload', onUnload);
      void flush();
    };
  }, [flush]);

  const setField = useCallback(
    (key: keyof ProjectInfoFields, value: string) => {
      if (!projectId) return;
      setFields((cur) => ({ ...cur, [key]: value }));

      if (pendingFor.current && pendingFor.current !== projectId) void flush();
      pending.current = { ...pending.current, [key]: value };
      pendingFor.current = projectId;

      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => void flush(), SAVE_DEBOUNCE_MS);
    },
    [projectId, flush],
  );

  return { fields, setField, saving, savedAt, error, flush };
}

export default useProjectInfo;
