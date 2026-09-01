import React from 'react';
import { Cpu, MemoryStick, CircuitBoard } from 'lucide-react';
import { useHardwareLive, type LiveGpu } from '@/hooks/useHardware';

/**
 * The small live gauges at the foot of the step navigator — CPU, RAM, and one
 * pair per GPU (utilisation and VRAM).
 *
 * **Why they are here rather than on a step.** A pipeline step is the wrong
 * home for them twice over: they describe the machine and not the project, and
 * the thing they are useful for is watching a run that has already put the
 * user somewhere else in the wizard. Under the navigator they are on screen
 * for every step and cost the page one 1.4 ms poll.
 *
 * Three rules the design follows, all of them about not lying in 190 px:
 *
 * * **A bar is coloured by what it means, not by its own value.** Load is
 *   cyan while it is ordinary, amber past 75 % and red past 90 %, because the
 *   thing worth seeing out of the corner of an eye is memory about to run out
 *   — a GPU pinned at 100 % during training is the tool working, and it is
 *   drawn calmly.
 * * **`null` is drawn as `—`, never as zero.** The first poll has no interval
 *   to average the CPU over (§ backend `_CpuTicks`), and a bar that opens at
 *   0 % states something nobody measured.
 * * **An integrated GPU says `shared`.** Its VRAM gauge divides by shared
 *   system memory rather than by the 128 MB stub it calls dedicated, so the
 *   label names the pool or the number is unreadable.
 */

const fmtGiB = (bytes: number | null | undefined): string =>
  bytes == null ? '—' : `${(bytes / 1024 ** 3).toFixed(1)}G`;

/** Cyan while ordinary, amber when it starts to matter, red when it is nearly out. */
const barColour = (pct: number | null): string => {
  if (pct == null) return 'bg-slate-700';
  if (pct >= 90) return 'bg-red-500';
  if (pct >= 75) return 'bg-amber-400';
  return 'bg-cyan-400';
};

const Gauge: React.FC<{
  icon?: React.ElementType;
  label: string;
  pct: number | null;
  value: string;
  title?: string;
}> = ({ icon: Icon, label, pct, value, title }) => (
  <div className="px-2 py-[3px]" title={title}>
    <div className="flex items-center gap-1 text-[10px] leading-none text-slate-400">
      {Icon ? <Icon className="w-3 h-3 shrink-0" /> : <span className="w-3 shrink-0" />}
      <span className="truncate">{label}</span>
      <span className="ml-auto tabular-nums text-slate-300">{value}</span>
    </div>
    <div className="mt-1 h-1 rounded-full bg-slate-800 overflow-hidden">
      <div
        className={`h-full rounded-full transition-[width] duration-500 ${barColour(pct)}`}
        style={{ width: `${Math.max(0, Math.min(100, pct ?? 0))}%` }}
      />
    </div>
  </div>
);

const GpuGauges: React.FC<{ gpu: LiveGpu }> = ({ gpu }) => {
  // "3D 42%, VideoDecode 8%" — which engine is busy is what tells FFmpeg's
  // hardware decode (§6.1) apart from a training run, and it costs a tooltip.
  const engines = Object.entries(gpu.engines)
    .filter(([, v]) => v >= 0.5)
    .map(([k, v]) => `${k} ${v.toFixed(0)}%`)
    .join(', ');

  return (
    <>
      <Gauge
        icon={CircuitBoard}
        label={gpu.name.replace(/^(NVIDIA|Intel\(R\)|AMD)\s+/i, '')}
        pct={gpu.utilization_pct}
        value={`${gpu.utilization_pct.toFixed(0)}%`}
        title={`${gpu.name} — ${gpu.kind}\nGPU ${gpu.utilization_pct.toFixed(1)}%${
          engines ? `\n${engines}` : ''
        }`}
      />
      <Gauge
        label={`VRAM${gpu.memory_pool === 'shared' ? ' (shared)' : ''}`}
        pct={gpu.memory_pct}
        value={`${fmtGiB(gpu.memory_used)}/${fmtGiB(gpu.memory_total)}`}
        title={`${gpu.name} — ${
          gpu.memory_pool === 'shared'
            ? 'integrated, so this is shared system memory'
            : 'dedicated video memory'
        }\n${(gpu.memory_used / 1024 ** 2).toFixed(0)} MB of ${(
          gpu.memory_total / 1024 ** 2
        ).toFixed(0)} MB`}
      />
    </>
  );
};

const HardwareGauges: React.FC = () => {
  const live = useHardwareLive(true, 1500);

  // Nothing at all to report — a non-Windows host, or the very first poll
  // still in flight. Drawing an empty frame would be worse than drawing
  // nothing, so the whole strip stays out of the layout.
  if (!live || !live.available) return null;

  const ram = live.memory;

  return (
    <div className="border-t border-slate-800 py-1 bg-slate-950/40">
      <Gauge
        icon={Cpu}
        label="CPU"
        pct={live.cpu_pct}
        value={live.cpu_pct == null ? '—' : `${live.cpu_pct.toFixed(0)}%`}
        title="Processor load across every core, averaged over the poll interval"
      />
      <Gauge
        icon={MemoryStick}
        label="RAM"
        pct={ram.load_pct}
        value={`${fmtGiB(ram.used)}/${fmtGiB(ram.total)}`}
        title={`${fmtGiB(ram.available)}B free of ${fmtGiB(ram.total)}B`}
      />
      {live.gpus.map((gpu) => (
        <GpuGauges key={gpu.luid} gpu={gpu} />
      ))}
    </div>
  );
};

export default HardwareGauges;
