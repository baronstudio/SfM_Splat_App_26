import React, { useEffect, useRef, useState } from 'react';
import { usePipelineStore } from '../../store/pipelineStore';

interface ProgressSample {
  ts: number;
  progress: number;
}

interface ProgressBarProps {
  step: string;
  label: string;
}

// How long a running step may report nothing before the bar stops pretending to
// know where it is. Long enough that an ordinary gap between two updates does
// not flicker the bar, short enough to be honest about a phase that never ticks
// — PySceneDetect decoding the whole source video, RealityScan reconstructing.
const STALE_AFTER_MS = 10_000;

function formatEta(seconds: number): string {
  if (seconds < 10) return 'Almost done…';
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m > 0 ? `~${m}m ${s}s remaining` : `~${s}s remaining`;
}

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return m > 0 ? `${m}m ${s}s elapsed` : `${s}s elapsed`;
}

// `curate` is step 2's second phase and reports under its own name, like the
// store's own map — without it the second bar of step 2 knew no status at all,
// so it never turned green and never went indeterminate. `masks` and `geometry`
// are the same shape one and two steps later, and every name here is present
// from the day its step is written rather than after the same bug a third time.
const stepNameToIndex: Record<string, number> = {
  extract: 2, curate: 2,
  sfm: 3, masks: 3,
  train: 4, geometry: 4,
  mesh: 5, export: 5,
  scene: 6,
};

export const ProgressBar: React.FC<ProgressBarProps> = ({ step, label }) => {
  const progress = usePipelineStore((s) => s.stepProgress[step] ?? 0);
  const stepStatuses = usePipelineStore((s) => s.stepStatuses);

  const stepIndex = stepNameToIndex[step];
  const stepStatus = stepIndex !== undefined ? stepStatuses[stepIndex] : undefined;
  const isDone = stepStatus === 'done';
  const isStepError = stepStatus === 'error';
  const isRunning = stepStatus === 'running';

  // ETA estimation via progress samples
  const samplesRef = useRef<ProgressSample[]>([]);
  const [eta, setEta] = useState<string | null>(null);

  // When the step started, and when it last said anything about where it is.
  // Both are what the indeterminate fallback is built on: a step that is
  // running and has not moved in STALE_AFTER_MS is a step whose bar is lying.
  const startedAtRef = useRef<number | null>(null);
  const lastProgressAtRef = useRef<number>(Date.now());
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!isRunning) {
      startedAtRef.current = null;
      return;
    }
    // A step restarting keeps the previous run's progress in the store until
    // its first message lands, so the clocks are reset here rather than on the
    // first tick.
    startedAtRef.current = Date.now();
    lastProgressAtRef.current = Date.now();
    samplesRef.current = [];
    setEta(null);

    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [isRunning, step]);

  useEffect(() => {
    lastProgressAtRef.current = Date.now();

    if (progress <= 0.05) {
      // Below 5 % the samples are dominated by whatever the step did before its
      // first real measurement, and an ETA extrapolated from them is nonsense.
      samplesRef.current = [];
      setEta(null);
      return;
    }
    if (progress >= 0.99) {
      setEta(null);
      return;
    }
    const ts = Date.now();
    samplesRef.current.push({ ts, progress });
    // Keep only the last 20 samples
    if (samplesRef.current.length > 20) {
      samplesRef.current = samplesRef.current.slice(-20);
    }
    const first = samplesRef.current[0];
    const elapsed = (ts - first.ts) / 1000; // seconds
    const progressDelta = progress - first.progress;
    if (progressDelta > 0.001 && elapsed > 0) {
      const remaining = (elapsed / progressDelta) * (1 - progress);
      setEta(formatEta(remaining));
    }
  }, [progress]);

  const stale = isRunning && now - lastProgressAtRef.current > STALE_AFTER_MS;
  const elapsedS =
    isRunning && startedAtRef.current !== null
      ? (now - startedAtRef.current) / 1000
      : 0;

  const pct = Math.round(progress * 100);

  const trackColor = isStepError
    ? 'bg-red-500/20'
    : isDone
    ? 'bg-green-500/20'
    : 'bg-cyan-500/20';

  const fillColor = isStepError
    ? 'bg-red-500'
    : isDone
    ? 'bg-green-500'
    : 'bg-cyan-500';

  return (
    <div className="w-full">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs font-medium text-slate-300">{label}</span>
        <div className="flex items-center gap-2">
          {stale ? (
            <>
              <span className="text-xs text-slate-500 italic">
                {formatElapsed(elapsedS)}
              </span>
              <span className="text-xs font-mono text-slate-400">
                {/* The percentage is still shown when the step reached one
                    before going quiet: "31 %, and nothing since" is more use
                    than a bar with no number at all. */}
                {pct > 0 ? `${pct}% · working…` : 'working…'}
              </span>
            </>
          ) : (
            <>
              {eta && <span className="text-xs text-slate-500 italic">{eta}</span>}
              <span className="text-xs font-mono text-slate-400">{pct}%</span>
            </>
          )}
        </div>
      </div>
      <div className={`w-full h-2 rounded-full overflow-hidden ${trackColor}`}>
        {stale ? (
          <div className={`h-2 w-full t4a-indeterminate ${fillColor} text-slate-900/40`} />
        ) : (
          <div
            className={`h-2 rounded-full transition-all duration-300 ${fillColor}`}
            style={{ width: `${Math.min(100, pct)}%` }}
          />
        )}
      </div>
    </div>
  );
};

export default ProgressBar;
