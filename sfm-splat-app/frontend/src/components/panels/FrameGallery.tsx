import React, { useMemo, useState } from 'react';
import { Check, Filter, RotateCcw, X } from 'lucide-react';
import type { FrameInfo, Override, SelectionSummary } from '../../types';

interface FrameGalleryProps {
  frames: FrameInfo[];
  loading?: boolean;
  analysed?: boolean;
  summary?: SelectionSummary | null;
  onOverride?: (filename: string, verdict: Override | null) => void;
  onDelete?: (filenames: string[]) => void;
}

const VRAM_MB_PER_FRAME = 5;

type FilterMode = 'all' | 'kept' | 'rejected' | 'warning' | 'overridden';

const REASON_LABEL: Record<string, string> = {
  blur: 'blur',
  redundant: 'redundant',
  manual: 'manual',
};

/** Border colour carries the verdict, so a full grid reads at a glance. */
const verdictBorder = (frame: FrameInfo): string => {
  if (frame.verdict === 'rejected') return 'border-red-600/70';
  if (frame.warning === 'gap') return 'border-amber-500/70';
  if (frame.verdict === 'kept') return 'border-green-600/50';
  return 'border-slate-700';
};

export const FrameGallery: React.FC<FrameGalleryProps> = ({
  frames,
  loading = false,
  analysed = false,
  summary = null,
  onOverride,
  onDelete,
}) => {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState<FilterMode>('all');

  const visible = useMemo(() => {
    switch (filter) {
      case 'kept': return frames.filter((f) => f.verdict === 'kept');
      case 'rejected': return frames.filter((f) => f.verdict === 'rejected');
      case 'warning': return frames.filter((f) => f.warning === 'gap');
      case 'overridden': return frames.filter((f) => f.override);
      default: return frames;
    }
  }, [frames, filter]);

  const toggleSelect = (filename: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(filename)) next.delete(filename);
      else next.add(filename);
      return next;
    });
  };

  const handleBulkDelete = () => {
    const toDelete = Array.from(selected);
    setSelected(new Set());
    onDelete?.(toDelete);
  };

  const vramEstimate = Math.round((frames.length * VRAM_MB_PER_FRAME) / 1024 * 10) / 10;
  const vramLabel = vramEstimate >= 1
    ? `~${vramEstimate}GB VRAM for RS`
    : `~${frames.length * VRAM_MB_PER_FRAME}MB VRAM for RS`;

  const filters: { id: FilterMode; label: string; count: number }[] = [
    { id: 'all', label: 'All', count: frames.length },
    { id: 'kept', label: 'Kept', count: summary?.kept ?? frames.filter((f) => f.verdict === 'kept').length },
    { id: 'rejected', label: 'Rejected', count: summary?.removed ?? frames.filter((f) => f.verdict === 'rejected').length },
    { id: 'warning', label: 'Gaps', count: summary?.warning_gap ?? frames.filter((f) => f.warning).length },
    { id: 'overridden', label: 'Manual', count: frames.filter((f) => f.override).length },
  ];

  return (
    <div className="flex flex-col gap-3">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-slate-300">
            {frames.length} frame{frames.length !== 1 ? 's' : ''} extracted
          </span>
          {frames.length > 0 && (
            <span className="text-xs bg-slate-800 text-slate-400 px-2 py-0.5 rounded">
              {vramLabel}
            </span>
          )}
          {!analysed && frames.length > 0 && (
            <span className="text-xs bg-slate-800 text-slate-500 px-2 py-0.5 rounded">
              not analysed yet
            </span>
          )}
        </div>
        {selected.size > 0 && (
          <button
            onClick={handleBulkDelete}
            className="text-xs bg-red-600 hover:bg-red-700 text-white px-3 py-1 rounded transition-colors"
          >
            Delete {selected.size} selected
          </button>
        )}
      </div>

      {/* Verdict filters — only meaningful once the analysis has run */}
      {analysed && (
        <div className="flex items-center gap-1 flex-wrap">
          <Filter className="w-3 h-3 text-slate-600 mr-1" />
          {filters.map((f) => (
            <button
              key={f.id}
              onClick={() => setFilter(f.id)}
              className={`text-xs px-2 py-0.5 rounded border transition-colors ${
                filter === f.id
                  ? 'bg-cyan-600/20 border-cyan-600 text-cyan-300'
                  : 'bg-slate-800 border-slate-700 text-slate-400 hover:border-slate-600'
              }`}
            >
              {f.label} <span className="text-slate-500">{f.count}</span>
            </button>
          ))}
        </div>
      )}

      {/* Grid */}
      {loading ? (
        <div className="grid grid-cols-5 gap-2">
          {Array.from({ length: 10 }).map((_, i) => (
            <div key={i} className="aspect-video bg-slate-800 rounded animate-pulse" />
          ))}
        </div>
      ) : visible.length === 0 ? (
        <div className="text-slate-600 italic text-sm">
          {frames.length === 0 ? 'No frames yet.' : 'No frame matches this filter.'}
        </div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-2">
          {visible.map((frame) => {
            const isSelected = selected.has(frame.filename);
            const rejected = frame.verdict === 'rejected';
            return (
              <div
                key={frame.filename}
                className={`relative group rounded overflow-hidden border-2 transition-colors ${
                  isSelected ? 'border-cyan-500' : verdictBorder(frame)
                }`}
              >
                <img
                  src={frame.url}
                  alt={frame.filename}
                  onClick={() => toggleSelect(frame.filename)}
                  className={`w-full aspect-video object-cover bg-slate-900 cursor-pointer transition-opacity ${
                    rejected ? 'opacity-40' : ''
                  }`}
                  loading="lazy"
                />

                {/* Verdict badge */}
                {rejected && (
                  <div className="absolute top-1 left-1 bg-red-600/90 text-white text-[10px] px-1 rounded">
                    ✕ {REASON_LABEL[frame.reason ?? ''] ?? 'rejected'}
                  </div>
                )}
                {!rejected && frame.warning === 'gap' && (
                  <div className="absolute top-1 left-1 bg-amber-500/90 text-white text-[10px] px-1 rounded">
                    ⚠ gap
                  </div>
                )}
                {frame.override && (
                  <div className="absolute bottom-6 left-1 bg-cyan-600/90 text-white text-[10px] px-1 rounded">
                    manual
                  </div>
                )}

                {/* Selection checkbox */}
                <div
                  onClick={() => toggleSelect(frame.filename)}
                  className="absolute top-1 right-1 cursor-pointer"
                >
                  <div
                    className={`w-4 h-4 rounded border-2 transition-colors ${
                      isSelected
                        ? 'bg-cyan-500 border-cyan-500'
                        : 'bg-slate-900/70 border-slate-500 opacity-0 group-hover:opacity-100'
                    }`}
                  />
                </div>

                {/* Manual override — always wins over the automatic verdict (§5) */}
                {onOverride && (
                  <div className="absolute inset-x-0 bottom-6 flex justify-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      title="Force keep"
                      onClick={() => onOverride(frame.filename, frame.override === 'keep' ? null : 'keep')}
                      className={`p-1 rounded ${
                        frame.override === 'keep'
                          ? 'bg-green-600 text-white'
                          : 'bg-slate-900/90 text-green-400 hover:bg-green-700 hover:text-white'
                      }`}
                    >
                      <Check className="w-3 h-3" />
                    </button>
                    <button
                      title="Force drop"
                      onClick={() => onOverride(frame.filename, frame.override === 'drop' ? null : 'drop')}
                      className={`p-1 rounded ${
                        frame.override === 'drop'
                          ? 'bg-red-600 text-white'
                          : 'bg-slate-900/90 text-red-400 hover:bg-red-700 hover:text-white'
                      }`}
                    >
                      <X className="w-3 h-3" />
                    </button>
                    {frame.override && (
                      <button
                        title="Back to the automatic verdict"
                        onClick={() => onOverride(frame.filename, null)}
                        className="p-1 rounded bg-slate-900/90 text-slate-300 hover:bg-slate-700"
                      >
                        <RotateCcw className="w-3 h-3" />
                      </button>
                    )}
                  </div>
                )}

                <div className="flex justify-between items-center text-[10px] text-slate-500 px-1 py-0.5 bg-slate-950">
                  <span className="truncate">{frame.filename}</span>
                  {frame.displacement_pct !== null && (
                    <span className="text-slate-600 shrink-0" title="ORB displacement vs last kept frame">
                      {frame.displacement_pct.toFixed(1)}%
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default FrameGallery;
