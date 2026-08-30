import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as THREE from 'three';
import client from '@/api/client';
import { usePipelineStore } from '@/store/pipelineStore';
import { usePipeline } from '@/hooks/usePipeline';
import {
  MAX_CROP_VOLUMES, defaultVolume, fromStored, sameVolumes, toStored,
  type CropGizmoMode, type CropKind, type CropMode, type CropVolume,
} from '@/components/viewer/cropVolumes';
import type { CropState, Project } from '@/types';

/**
 * The crop tool's state (CLAUDE.md §7.6b): the volumes, where they are stored,
 * and the one call that turns them into a file.
 *
 * ── Where the volumes live, and why it is not `defaults.json` ───────────────
 *
 * They go in `Project.settings_json` under `crop`, which is layer 3 of §4's
 * settings model — but unlike every other section there is no `defaults.json`
 * counterpart, because a volume placed around *this* scene is not a default
 * anything could inherit. What it is, is project data that must **outlive a
 * step 4 reset**: `train/crop/` goes with `train/` when step 4 is re-run
 * (§14.1), and it should, since the splat it was cut from has just been
 * deleted — but the frame is set by the sparse model, which a re-train does not
 * touch, so the volumes are still exactly right and asking for them to be
 * placed again with the gizmo would be gratuitous.
 *
 * The save is the same 300 ms debounced PATCH of a diff that the settings
 * panels use, for the same reason: a gizmo drag fires a change per pixel, and a
 * panel that must be saved is a panel that gets lost.
 */

const SAVE_DEBOUNCE_MS = 300;

export interface CropTool {
  volumes: CropVolume[];
  selectedId: string | null;
  gizmoMode: CropGizmoMode;
  showVolumes: boolean;
  livePreview: boolean;
  liveSupported: boolean;
  state: CropState | null;
  /** Volumes edited since the crop on disk was written. */
  dirty: boolean;
  running: boolean;
  error: string | null;
  select(id: string | null): void;
  add(kind: CropKind, mode: CropMode, flipUp: boolean): void;
  update(volume: CropVolume): void;
  remove(id: string): void;
  clear(): void;
  setGizmoMode(mode: CropGizmoMode): void;
  setShowVolumes(show: boolean): void;
  setLivePreview(live: boolean): void;
  setLiveSupported(supported: boolean): void;
  /** The framed scene, so a new volume lands where the user is looking. */
  setBounds(centre: [number, number, number], radius: number): void;
  apply(): Promise<void>;
  refresh(): void;
}

export function useCrop(
  projectId: string | null,
  enabled: boolean,
  /**
   * Wizard step 4's status. The crop pass attaches to that step and hands its
   * previous status back when it finishes (`_run_attached_pass`), so a run is
   * `running` and then whatever it was before — which is both the signal that
   * the pass is over and the cue to re-read what it wrote.
   */
  stepStatus: string | undefined,
): CropTool {
  const upsertProject = usePipelineStore((s) => s.upsertProject);
  const { runCrop } = usePipeline();

  const [volumes, setVolumes] = useState<CropVolume[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [gizmoMode, setGizmoMode] = useState<CropGizmoMode>('translate');
  const [showVolumes, setShowVolumes] = useState(true);
  const [livePreview, setLivePreview] = useState(true);
  const [liveSupported, setLiveSupported] = useState(true);
  const [state, setState] = useState<CropState | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const bounds = useRef<{ centre: THREE.Vector3; radius: number } | null>(null);

  // ── Load ──────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!projectId || !enabled) {
      setState(null);
      setVolumes([]);
      return undefined;
    }
    let cancelled = false;
    client
      .get<CropState>(`/files/${projectId}/crop`)
      .then((res) => {
        if (cancelled) return;
        setState(res.data);
        setVolumes(fromStored(res.data.volumes));
        setError(null);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setState(null);
        setError(e instanceof Error ? e.message : 'Failed to read the crop state');
      });
    return () => { cancelled = true; };
  }, [projectId, enabled, nonce, stepStatus]);

  // ── Save ──────────────────────────────────────────────────────────────────
  const pending = useRef<CropVolume[] | null>(null);
  const pendingFor = useRef<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flush = useCallback(async () => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    const next = pending.current;
    const id = pendingFor.current;
    pending.current = null;
    if (!id || next === null) return;
    try {
      const res = await client.patch<Project>(`/projects/${id}`, {
        settings: { crop: { volumes: toStored(next) } },
      });
      upsertProject(res.data);
    } catch (e) {
      // Put it back rather than lose it — the next edit or flush retries.
      if (pending.current === null) {
        pending.current = next;
        pendingFor.current = id;
      }
      setError(e instanceof Error ? e.message : 'Failed to save the crop volumes');
    }
  }, [upsertProject]);

  const store = useCallback((next: CropVolume[]) => {
    setVolumes(next);
    if (!projectId) return;
    if (pendingFor.current && pendingFor.current !== projectId) void flush();
    pending.current = next;
    pendingFor.current = projectId;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => { void flush(); }, SAVE_DEBOUNCE_MS);
  }, [projectId, flush]);

  // A step page unmounts the moment the user changes step; neither that nor a
  // reload may swallow a drag still sitting in the debounce.
  useEffect(() => {
    const onUnload = () => {
      const next = pending.current;
      const id = pendingFor.current;
      if (!id || next === null) return;
      pending.current = null;
      fetch(`${client.defaults.baseURL ?? '/api'}/projects/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ settings: { crop: { volumes: toStored(next) } } }),
        keepalive: true,
      }).catch(() => {});
    };
    window.addEventListener('beforeunload', onUnload);
    return () => {
      window.removeEventListener('beforeunload', onUnload);
      void flush();
    };
  }, [flush]);

  // ── Editing ───────────────────────────────────────────────────────────────
  const add = useCallback((kind: CropKind, mode: CropMode, flipUp: boolean) => {
    if (volumes.length >= MAX_CROP_VOLUMES) return;
    // Sized and centred on what the viewer has framed, so a new volume lands
    // around the scene rather than as a speck at the origin of a model whose
    // own scale the mapper chose (`[orient] ... scaled by 0.3534`).
    const volume = defaultVolume(
      kind, mode,
      bounds.current?.centre ?? null,
      bounds.current?.radius ?? 1,
      flipUp,
    );
    store([...volumes, volume]);
    setSelectedId(volume.id);
  }, [volumes, store]);

  const update = useCallback((volume: CropVolume) => {
    store(volumes.map((v) => (v.id === volume.id ? volume : v)));
  }, [volumes, store]);

  const remove = useCallback((id: string) => {
    store(volumes.filter((v) => v.id !== id));
    setSelectedId((current) => (current === id ? null : current));
  }, [volumes, store]);

  const clear = useCallback(() => {
    store([]);
    setSelectedId(null);
  }, [store]);

  // ── Apply ─────────────────────────────────────────────────────────────────
  const apply = useCallback(async () => {
    if (!projectId) return;
    setRunning(true);
    setError(null);
    try {
      // The debounced PATCH first: the pass reads the volumes out of
      // `settings_json`, so starting it with an edit still in the debounce
      // would cut to the *previous* stack and report success.
      await flush();
      await runCrop(projectId, { crop: { volumes: toStored(volumes) } });
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      setError(detail ?? (e instanceof Error ? e.message : 'Failed to start the crop'));
      setRunning(false);
    }
  }, [projectId, volumes, flush, runCrop]);

  // The pass reports on the WebSocket like every other step, so the end of a
  // run is step 4 leaving `running` — not the POST resolving, which only means
  // the task was accepted.
  const sawRunning = useRef(false);
  useEffect(() => {
    if (stepStatus === 'running') {
      sawRunning.current = true;
    } else if (sawRunning.current) {
      sawRunning.current = false;
      setRunning(false);
    }
  }, [stepStatus]);

  const dirty = useMemo(() => {
    const applied = state?.applied?.volumes;
    if (!state?.cropped) return false;
    return !sameVolumes(volumes, fromStored(applied));
  }, [volumes, state]);

  return {
    volumes, selectedId, gizmoMode, showVolumes, livePreview, liveSupported,
    state, dirty, running, error,
    select: setSelectedId,
    add, update, remove, clear,
    setGizmoMode, setShowVolumes, setLivePreview, setLiveSupported,
    setBounds: (centre, radius) => {
      bounds.current = { centre: new THREE.Vector3(...centre), radius };
    },
    apply,
    refresh: () => setNonce((n) => n + 1),
  };
}

export default useCrop;
