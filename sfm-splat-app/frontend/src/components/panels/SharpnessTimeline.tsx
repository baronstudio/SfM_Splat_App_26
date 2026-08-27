import React, { useMemo } from 'react';
import {
  CartesianGrid,
  Line,
  ComposedChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { CurationScores } from '../../types';

interface SharpnessTimelineProps {
  scores: CurationScores;
  className?: string;
}

interface Point {
  index: number;
  filename: string;
  sharpness: number;
  median: number;
  /** Only set on rejected frames, so they render as a scatter overlay. */
  blur?: number;
  redundant?: number;
  gap?: number;
  displacement: number | null;
}

const COLORS = {
  sharpness: '#06b6d4',
  median: '#64748b',
  blur: '#ef4444',
  redundant: '#a855f7',
  gap: '#f59e0b',
  cut: '#f8fafc',
};

const TooltipBody: React.FC<{ active?: boolean; payload?: { payload: Point }[] }> = ({ active, payload }) => {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="bg-slate-950 border border-slate-700 rounded px-2 py-1 text-xs text-slate-300">
      <div className="text-slate-100 font-medium">{p.filename}</div>
      <div>sharpness {Math.round(p.sharpness).toLocaleString()}</div>
      <div className="text-slate-500">local median {Math.round(p.median).toLocaleString()}</div>
      {p.displacement !== null && <div>step {p.displacement.toFixed(1)}%</div>}
      {p.blur !== undefined && <div className="text-red-400">rejected — blur</div>}
      {p.redundant !== undefined && <div className="text-purple-400">rejected — redundant</div>}
      {p.gap !== undefined && <div className="text-amber-400">warning — gap</div>}
    </div>
  );
};

/**
 * Sharpness over the frame index, with the rolling median it is judged against
 * and a marker at every cut (CLAUDE.md §6.4).
 *
 * Plotting the median alongside the raw score is the point: rejection is
 * relative, so a bare score curve would not show *why* a frame was dropped.
 */
export const SharpnessTimeline: React.FC<SharpnessTimelineProps> = ({ scores, className }) => {
  const data = useMemo<Point[]>(
    () =>
      scores.frames.map((f) => ({
        index: f.index,
        filename: f.filename,
        sharpness: f.sharpness,
        median: f.sharpness_median,
        displacement: f.displacement_pct,
        ...(f.auto_reason === 'blur' ? { blur: f.sharpness } : {}),
        ...(f.auto_reason === 'redundant' ? { redundant: f.sharpness } : {}),
        ...(f.warning === 'gap' ? { gap: f.sharpness } : {}),
      })),
    [scores],
  );

  // The first sequence starts at 0 and is not a cut.
  const cuts = useMemo(
    () => scores.sequences.filter((s) => s.start_index > 0).map((s) => s.start_index),
    [scores],
  );

  return (
    <div className={className}>
      <div className="flex items-center justify-between mb-1 flex-wrap gap-2">
        <span className="text-sm font-semibold text-slate-300">Sharpness timeline</span>
        <div className="flex items-center gap-3 text-[10px] text-slate-500">
          <span><span style={{ color: COLORS.sharpness }}>—</span> Tenengrad</span>
          <span><span style={{ color: COLORS.median }}>—</span> rolling median</span>
          <span><span style={{ color: COLORS.blur }}>●</span> blur</span>
          <span><span style={{ color: COLORS.redundant }}>●</span> redundant</span>
          <span><span style={{ color: COLORS.gap }}>●</span> gap</span>
          {cuts.length > 0 && <span><span style={{ color: COLORS.cut }}>┆</span> cut</span>}
        </div>
      </div>

      <div className="h-[200px] w-full bg-slate-900 rounded border border-slate-800 p-2">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
            <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
            <XAxis
              dataKey="index"
              stroke="#475569"
              tick={{ fontSize: 10, fill: '#64748b' }}
              label={{ value: 'frame', position: 'insideBottomRight', fontSize: 10, fill: '#475569' }}
            />
            <YAxis
              stroke="#475569"
              tick={{ fontSize: 10, fill: '#64748b' }}
              width={48}
              tickFormatter={(v: number) => (v >= 1000 ? `${Math.round(v / 1000)}k` : String(Math.round(v)))}
            />
            <Tooltip content={<TooltipBody />} />

            {cuts.map((c) => (
              <ReferenceLine
                key={c}
                x={c}
                stroke={COLORS.cut}
                strokeDasharray="2 3"
                strokeOpacity={0.6}
              />
            ))}

            <Line type="monotone" dataKey="median" stroke={COLORS.median} dot={false} strokeWidth={1} strokeDasharray="4 3" isAnimationActive={false} />
            <Line type="monotone" dataKey="sharpness" stroke={COLORS.sharpness} dot={false} strokeWidth={1.5} isAnimationActive={false} />
            <Scatter dataKey="blur" fill={COLORS.blur} isAnimationActive={false} />
            <Scatter dataKey="redundant" fill={COLORS.redundant} isAnimationActive={false} />
            <Scatter dataKey="gap" fill={COLORS.gap} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};

export default SharpnessTimeline;
