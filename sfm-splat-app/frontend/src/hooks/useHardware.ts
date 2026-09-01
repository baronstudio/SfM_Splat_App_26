import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * What this machine is (`/api/hardware/`) and what it is doing
 * (`/api/hardware/live`) — CLAUDE.md §4's installation layer, drawn in the
 * global setup panel and under the step navigator.
 *
 * **One poller for the whole page, not one per component.** The sidebar gauges
 * and the setup panel's Hardware section want the same tick, and two hooks
 * each running their own `setInterval` would double the request rate and — far
 * worse — halve the interval each of them *averages over*, because the backend
 * keeps a single PDH query whose readings are the delta since whoever polled
 * last. Two consumers on a 1 s interval would have made both of them report a
 * 500 ms average and disagree with each other. So the timer, the last sample
 * and the subscriber list live in this module and every hook reads the same
 * one. This is the `useWebSocket` lesson of §12 (2026-08-28) one hook along:
 * a second socket was a second, wrong copy of the same stream.
 *
 * **It stops when nothing is looking.** No subscribers, or a hidden tab, and
 * the timer is cleared — a gauge nobody can see should not poll a workstation
 * that is busy training.
 */

export interface HardwareCpu {
  name: string | null;
  architecture: string | null;
  physical_cores: number | null;
  logical_cores: number | null;
  base_clock_mhz: number | null;
  identifier: string | null;
  vendor: string | null;
}

export interface HardwareMemory {
  total: number | null;
  available: number | null;
  used: number | null;
  load_pct: number | null;
  commit_total: number | null;
  commit_available: number | null;
}

export interface HardwareAdapter {
  luid: number;
  luid_hex: string;
  name: string;
  kind: 'discrete' | 'integrated' | 'software';
  dedicated_video_memory: number;
  shared_system_memory: number;
  memory_pool: 'dedicated' | 'shared';
  memory_budget: number;
  live: boolean;
  driver_version: string | null;
  driver_date: string | null;
  board_memory_bytes: number | null;
}

export interface SpirulaDevice {
  index: number;
  name: string;
  type: string;
  vram: string;
  status: string;
}

export interface HardwareStatic {
  available: boolean;
  reason: string | null;
  platform: Record<string, string>;
  cpu: HardwareCpu;
  memory: HardwareMemory;
  adapters: HardwareAdapter[];
  spirula: { available: boolean; reason: string | null; devices: SpirulaDevice[] };
  gpu_counters: { available: boolean; reason: string | null; source: string | null };
}

export interface LiveGpu {
  luid: number;
  name: string;
  kind: 'discrete' | 'integrated';
  utilization_pct: number;
  engines: Record<string, number>;
  memory_pool: 'dedicated' | 'shared';
  memory_used: number;
  memory_total: number;
  memory_pct: number | null;
  dedicated_used: number;
  shared_used: number;
}

export interface HardwareLive {
  available: boolean;
  cpu_pct: number | null;
  memory: HardwareMemory;
  gpus: LiveGpu[];
}

/* ── The single shared poller ───────────────────────────────────────────── */

type Listener = (sample: HardwareLive | null) => void;

const listeners = new Set<Listener>();
let timer: ReturnType<typeof setInterval> | null = null;
let latest: HardwareLive | null = null;
let inFlight = false;
let intervalMs = 1500;

const tick = async () => {
  // Never stack requests: a poll that outlives its interval (a machine under
  // full load) must not queue a second one behind it.
  if (inFlight) return;
  inFlight = true;
  try {
    const res = await fetch('/api/hardware/live');
    if (res.ok) {
      latest = (await res.json()) as HardwareLive;
      listeners.forEach((fn) => fn(latest));
    }
  } catch {
    /* A gauge is not worth an error toast. It simply stops moving. */
  } finally {
    inFlight = false;
  }
};

const shouldRun = () =>
  listeners.size > 0 && (typeof document === 'undefined' || !document.hidden);

const sync = () => {
  if (shouldRun() && timer === null) {
    void tick();
    timer = setInterval(() => void tick(), intervalMs);
  } else if (!shouldRun() && timer !== null) {
    clearInterval(timer);
    timer = null;
  }
};

if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', sync);
}

/** Live gauges. `active` false unsubscribes, which stops the timer if nobody else wants it. */
export const useHardwareLive = (active = true, periodMs = 1500) => {
  const [sample, setSample] = useState<HardwareLive | null>(latest);

  useEffect(() => {
    if (!active) return;
    intervalMs = Math.min(intervalMs, periodMs);
    const listener: Listener = (s) => setSample(s);
    listeners.add(listener);
    sync();
    return () => {
      listeners.delete(listener);
      sync();
    };
  }, [active, periodMs]);

  return sample;
};

/** The machine's description. Cached by the backend, so this is one call. */
export const useHardwareStatic = (active = true) => {
  const [info, setInfo] = useState<HardwareStatic | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fetched = useRef(false);

  const load = useCallback(async (refresh = false) => {
    setLoading(true);
    try {
      const res = await fetch(`/api/hardware/${refresh ? '?refresh=1' : ''}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setInfo((await res.json()) as HardwareStatic);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to read the hardware.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!active || fetched.current) return;
    fetched.current = true;
    void load();
  }, [active, load]);

  return { info, loading, error, refresh: () => load(true) };
};
