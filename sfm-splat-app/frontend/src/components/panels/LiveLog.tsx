import React, { useEffect, useRef, useState } from 'react';
import { usePipelineStore } from '../../store/pipelineStore';
import type { LogLevel } from '../../types';

const LEVEL_COLOR: Record<LogLevel, string> = {
  INFO: 'text-slate-300',
  WARNING: 'text-yellow-400',
  ERROR: 'text-red-400',
  SUCCESS: 'text-green-400',
  DEBUG: 'text-cyan-700',
};

function formatTimestamp(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toTimeString().slice(0, 8);
  } catch {
    return iso.slice(11, 19);
  }
}

interface LiveLogProps {
  className?: string;
}

export const LiveLog: React.FC<LiveLogProps> = ({ className }) => {
  const logs = usePipelineStore((s) => s.logs);
  const clearLogs = usePipelineStore((s) => s.clearLogs);
  const wsConnected = usePipelineStore((s) => s.wsConnected);
  const containerRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const [showDebug, setShowDebug] = useState(true);

  // Auto-scroll to bottom when new logs arrive, unless user scrolled up
  useEffect(() => {
    if (!autoScroll) return;
    const el = containerRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [logs.length, autoScroll]);

  const visibleLogs = showDebug ? logs : logs.filter((e) => e.level !== 'DEBUG');

  const handleScroll = () => {
    const el = containerRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 32;
    setAutoScroll(atBottom);
  };

  return (
    <div className={`flex flex-col h-full ${className ?? ''}`}>
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-slate-900 border-b border-slate-800 rounded-t">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-slate-400 uppercase tracking-widest">Live Log</span>
          <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs bg-slate-700 text-slate-300">
            {visibleLogs.length}
          </span>
          {/* WebSocket connection indicator */}
          <span
            className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-medium ${
              wsConnected
                ? 'bg-green-950 text-green-400'
                : 'bg-red-950 text-red-400'
            }`}
            title={wsConnected ? 'WebSocket connected' : 'WebSocket disconnected'}
          >
            <span
              className={`inline-block w-1.5 h-1.5 rounded-full ${
                wsConnected ? 'bg-green-400 animate-pulse' : 'bg-red-400'
              }`}
            />
            {wsConnected ? 'WS' : 'WS ✕'}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {/* Toggle DEBUG messages */}
          <button
            onClick={() => setShowDebug((v) => !v)}
            className={`text-xs transition-colors px-2 py-0.5 rounded hover:bg-slate-800 ${
              showDebug ? 'text-cyan-600 hover:text-cyan-400' : 'text-slate-500 hover:text-slate-200'
            }`}
            title={showDebug ? 'Hide DEBUG messages' : 'Show DEBUG messages'}
          >
            DBG
          </button>
          <button
            onClick={clearLogs}
            className="text-xs text-slate-500 hover:text-slate-200 transition-colors px-2 py-0.5 rounded hover:bg-slate-800"
          >
            Clear
          </button>
        </div>
      </div>

      {/* Terminal body */}
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto bg-slate-950 p-3 rounded-b"
        style={{ fontFamily: "'JetBrains Mono', 'Fira Mono', monospace", fontSize: '0.8rem' }}
      >
        {visibleLogs.length === 0 ? (
          <span className="text-slate-500 italic">
            {wsConnected ? 'No logs yet — start a pipeline step to see output.' : 'Waiting for WebSocket connection…'}
          </span>
        ) : (
          visibleLogs.map((entry) => {
            const color = LEVEL_COLOR[entry.level as LogLevel] ?? 'text-slate-400';
            return (
              <div key={entry.id} className={`leading-5 whitespace-pre-wrap break-all ${color}`}>
                <span className="text-slate-600">[{formatTimestamp(entry.timestamp)}]</span>
                {' '}
                <span className="text-slate-500">[{String(entry.step).toUpperCase()}]</span>
                {' '}
                {entry.message}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};

export default LiveLog;

