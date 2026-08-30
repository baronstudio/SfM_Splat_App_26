import { useState, useEffect, useRef } from 'react';
import { usePipelineStore, stepNameToIndex } from '../store/pipelineStore';
import apiClient from '../api/client';
import type { Project, StepName, WsMessage } from '../types';

/**
 * Same origin as the page, for the reason `api/client.ts` carries: an absolute
 * `ws://localhost:8000` opens a socket on the *viewer's* machine, so the
 * LiveLog and every progress bar are dead for anyone but the operator sitting
 * at the server. Vite proxies `/ws` (vite.config.ts), and `wss:` follows
 * automatically if the page is ever served over TLS.
 */
const WS_URL = (() => {
  const base = import.meta.env.VITE_WS_BASE;
  if (base) return `${base.replace(/\/$/, '')}/ws/logs`;
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}/ws/logs`;
})();
const MAX_RETRIES = 5;

export const useWebSocket = () => {
  const [connected, setConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState<WsMessage | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const retriesRef = useRef(0);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Tracks whether the hook is intentionally mounted — prevents StrictMode's
  // cleanup→remount cycle from spawning a second connection via auto-reconnect.
  const mountedRef = useRef(false);

  const connect = () => {
    // CONNECTING counts as "already have one". Testing only for OPEN left a
    // window one tick wide that StrictMode walks straight into: mount opens
    // ws1, the cleanup closes it while it is still CONNECTING, and the second
    // mount sees a non-OPEN socket and opens ws2 beside it — two live sockets,
    // so every line the bus sent arrived in the LiveLog twice. Measured on a
    // training run: the backend broadcast each `step N/M` line exactly once
    // (counted on a lone socket) while the page showed each of them twice.
    const existing = wsRef.current;
    if (
      existing
      && (existing.readyState === WebSocket.OPEN
        || existing.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }

    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      retriesRef.current = 0;
      // Notify store + add a visible log entry so the LiveLog is never empty
      usePipelineStore.getState().setWsConnected(true);
      usePipelineStore.getState().addLog({
        id: `ws-connect-${Date.now()}`,
        timestamp: new Date().toISOString(),
        step: 'pipeline',
        level: 'DEBUG',
        message: `[WS] ✅ WebSocket connected → ${WS_URL}`,
      });
    };

    ws.onmessage = (event: MessageEvent) => {
      try {
        const msg = JSON.parse(event.data as string) as WsMessage;
        setLastMessage(msg);

        usePipelineStore.getState().handleWsMessage(msg);

        // After a step finishes (success or error), persist its status and the
        // wizard position. **Only the step this message is about is written**,
        // merged onto the row the backend already holds — the whole in-memory
        // dict used to go out, which turned any staleness in the store into DB
        // truth for a project that had never run those steps (see
        // `setCurrentProject`). The row is the authority for every other step:
        // an attached pass restores the step it borrowed (§7.4, §7.5), and a
        // reset pops keys, neither of which this event knows about.
        if (msg.type === 'status' && (msg.level === 'SUCCESS' || msg.level === 'ERROR')) {
          const state = usePipelineStore.getState();
          const projectId = state.currentProjectId;
          const stepIdx = stepNameToIndex[msg.step as StepName];
          if (projectId && stepIdx !== undefined) {
            const persisted = state.projects.find((p) => p.id === projectId)?.step_status ?? {};
            const stepStatusDict: Record<string, string> = {
              ...persisted,
              [String(stepIdx)]: state.stepStatuses[stepIdx],
            };
            apiClient.put<Project>(`/projects/${projectId}`, {
              step_status: stepStatusDict,
              current_step: state.currentStep,
            })
              // Keep the local row in step with what was just written, or the
              // next merge would start from a stale copy of it.
              .then((res) => usePipelineStore.getState().upsertProject(res.data))
              .catch(() => { /* persistence is best-effort */ });
          }
        }
      } catch {
        // malformed message — ignore
      }
    };

    ws.onclose = () => {
      // Only the socket that is still the current one gets to report its own
      // loss and ask for a reconnect. A socket already replaced by a later
      // connect() is history, and letting it run this handler would clear the
      // live `wsRef` and schedule a second connection on top of it.
      if (wsRef.current !== ws) return;

      setConnected(false);
      usePipelineStore.getState().setWsConnected(false);
      usePipelineStore.getState().addLog({
        id: `ws-disconnect-${Date.now()}`,
        timestamp: new Date().toISOString(),
        step: 'pipeline',
        level: 'WARNING',
        message: '[WS] ⚠️ WebSocket disconnected — attempting reconnect...',
      });
      wsRef.current = null;

      // Only auto-reconnect if the component is still intentionally mounted
      if (mountedRef.current && retriesRef.current < MAX_RETRIES) {
        const delay = Math.min(1000 * 2 ** retriesRef.current, 30_000);
        retriesRef.current += 1;
        timeoutRef.current = setTimeout(connect, delay);
      }
    };

    ws.onerror = () => {
      ws.close();
    };
  };

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
      wsRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { connected, lastMessage };
};

export default useWebSocket;
