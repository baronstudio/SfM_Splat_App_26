import React from 'react';
import {
  AlertTriangle,
  Check,
  Cpu,
  MemoryStick,
  CircuitBoard,
  RefreshCw,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  useHardwareLive,
  useHardwareStatic,
  type HardwareAdapter,
  type LiveGpu,
} from '@/hooks/useHardware';

/**
 * Hardware — what this workstation is, and what it is doing right now.
 *
 * It sits in the **global setup panel** beside Tools and Checkpoints, and for
 * the same reason (CLAUDE.md §4): a CPU is a property of this machine, like
 * where FFmpeg is, and it has no `defaults.json` section to override because
 * there is nothing here anybody can set.
 *
 * The one thing this panel is *for*, beyond curiosity, is §1's central claim.
 * This app exists because spirula is Vulkan rather than CUDA, so it runs on
 * Intel, AMD and Apple silicon; the proof of that on any given machine is
 * `spirula sam devices`, and it is drawn here next to the adapters Windows
 * reports so the two can be compared by eye.
 *
 * **Every number is read, never remembered** (§2.7). The adapters come from
 * DXGI's own registry, the live figures from Windows performance counters, and
 * the Vulkan verdict from the installed binary — so a driver update or a new
 * card shows up here without anything in this app being edited.
 */

const fmtBytes = (n: number | null | undefined): string => {
  if (n == null || n <= 0) return '—';
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(2)} GB`;
  return `${Math.round(n / 1024 ** 2)} MB`;
};

const fmtMHz = (mhz: number | null): string =>
  mhz == null ? '—' : mhz >= 1000 ? `${(mhz / 1000).toFixed(2)} GHz` : `${mhz} MHz`;

const barColour = (pct: number | null): string => {
  if (pct == null) return 'bg-slate-700';
  if (pct >= 90) return 'bg-red-500';
  if (pct >= 75) return 'bg-amber-400';
  return 'bg-cyan-400';
};

const Bar: React.FC<{ pct: number | null; className?: string }> = ({ pct, className }) => (
  <div className={`h-1.5 rounded-full bg-slate-800 overflow-hidden ${className ?? ''}`}>
    <div
      className={`h-full rounded-full transition-[width] duration-500 ${barColour(pct)}`}
      style={{ width: `${Math.max(0, Math.min(100, pct ?? 0))}%` }}
    />
  </div>
);

/** A read-only fact. This whole panel is facts — there is nothing to set. */
const Fact: React.FC<{ label: string; value: React.ReactNode; hint?: string }> = ({
  label,
  value,
  hint,
}) => (
  <div className="flex items-baseline justify-between gap-6 py-1.5">
    <div className="min-w-0">
      <span className="text-sm text-slate-300">{label}</span>
      {hint && <p className="text-xs text-slate-500 leading-snug">{hint}</p>}
    </div>
    <span className="shrink-0 text-sm text-slate-100 tabular-nums text-right max-w-[420px] truncate">
      {value}
    </span>
  </div>
);

const Card: React.FC<{
  icon: React.ElementType;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}> = ({ icon: Icon, title, subtitle, children }) => (
  <section className="rounded-lg border border-slate-800 bg-slate-900/40 p-4 mb-4">
    <header className="flex items-center gap-2 mb-2">
      <Icon className="w-4 h-4 text-cyan-400 shrink-0" />
      <h3 className="text-sm font-medium text-slate-100">{title}</h3>
      {subtitle && <span className="text-xs text-slate-500 truncate">{subtitle}</span>}
    </header>
    {children}
  </section>
);

const GpuCard: React.FC<{ adapter: HardwareAdapter; live: LiveGpu | undefined }> = ({
  adapter,
  live,
}) => (
  <Card
    icon={CircuitBoard}
    title={adapter.name}
    subtitle={adapter.kind === 'discrete' ? 'discrete' : adapter.kind}
  >
    {live ? (
      <div className="grid grid-cols-2 gap-4 mb-3">
        <div>
          <div className="flex items-baseline justify-between text-xs text-slate-400 mb-1">
            <span>GPU</span>
            <span className="tabular-nums text-slate-200">
              {live.utilization_pct.toFixed(1)} %
            </span>
          </div>
          <Bar pct={live.utilization_pct} />
          <p className="text-[11px] text-slate-500 mt-1 truncate">
            {Object.entries(live.engines)
              .filter(([, v]) => v >= 0.5)
              .map(([k, v]) => `${k} ${v.toFixed(0)}%`)
              .join(' · ') || 'idle'}
          </p>
        </div>
        <div>
          <div className="flex items-baseline justify-between text-xs text-slate-400 mb-1">
            <span>VRAM{live.memory_pool === 'shared' ? ' (shared)' : ''}</span>
            <span className="tabular-nums text-slate-200">
              {fmtBytes(live.memory_used)} / {fmtBytes(live.memory_total)}
            </span>
          </div>
          <Bar pct={live.memory_pct} />
          <p className="text-[11px] text-slate-500 mt-1">
            {live.memory_pool === 'shared'
              ? 'Integrated: its working memory is shared system RAM.'
              : `Dedicated · also using ${fmtBytes(live.shared_used)} shared`}
          </p>
        </div>
      </div>
    ) : (
      <p className="text-xs text-slate-500 mb-3">
        {adapter.kind === 'software'
          ? 'Software adapter — no hardware behind it, so it is listed but never gauged.'
          : 'No live counter for this adapter. Windows keeps a registry entry per driver ' +
            'install, so an adapter that is not reporting is usually a superseded one.'}
      </p>
    )}

    <div className="divide-y divide-slate-800/70">
      <Fact label="Dedicated video memory" value={fmtBytes(adapter.dedicated_video_memory)} />
      <Fact label="Shared system memory" value={fmtBytes(adapter.shared_system_memory)} />
      {adapter.board_memory_bytes != null && (
        <Fact
          label="Board memory"
          value={fmtBytes(adapter.board_memory_bytes)}
          hint="The card's own total, which is larger than the budget the gauge divides by."
        />
      )}
      <Fact label="Driver" value={adapter.driver_version ?? '—'} />
      <Fact label="Driver date" value={adapter.driver_date ?? '—'} />
      <Fact label="Adapter LUID" value={adapter.luid_hex} />
    </div>
  </Card>
);

const HardwareSection: React.FC<{ active: boolean }> = ({ active }) => {
  const { info, loading, error, refresh } = useHardwareStatic(active);
  const live = useHardwareLive(active, 1500);

  if (error) return <p className="text-sm text-red-400">{error}</p>;
  if (!info) return <p className="text-slate-500 text-sm">Reading the hardware…</p>;

  const byLuid = new Map((live?.gpus ?? []).map((g) => [g.luid, g]));
  // `live` is the filter, and it has to be. DXGI's registry keeps one key per
  // driver install and never removes the old one — this machine has four
  // entries for one Intel UHD 770, all sharing its name and therefore its
  // driver version, so anything softer than this drew the same card four
  // times. An adapter Windows reports no performance counter for does not
  // exist any more.
  const adapters = info.adapters.filter((a) => a.live);
  const ram = live?.memory ?? info.memory;

  return (
    <div>
      <div className="flex items-start justify-between gap-4 mb-4">
        <p className="text-xs text-slate-500 leading-snug max-w-[560px]">
          Read off this machine, never configured. The gauges refresh while this panel is open;
          everything else is read once per server start.
        </p>
        <Button
          variant="outline"
          size="sm"
          onClick={refresh}
          disabled={loading}
          className="shrink-0"
        >
          <RefreshCw className={`w-3.5 h-3.5 mr-2 ${loading ? 'animate-spin' : ''}`} />
          Re-read
        </Button>
      </div>

      {!info.available && (
        <p className="text-sm text-amber-400 mb-4 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          {info.reason}
        </p>
      )}

      <Card icon={Cpu} title={info.cpu.name ?? 'Processor'} subtitle={info.cpu.vendor ?? undefined}>
        {live && (
          <div className="mb-3">
            <div className="flex items-baseline justify-between text-xs text-slate-400 mb-1">
              <span>Load</span>
              <span className="tabular-nums text-slate-200">
                {live.cpu_pct == null ? '—' : `${live.cpu_pct.toFixed(1)} %`}
              </span>
            </div>
            <Bar pct={live.cpu_pct} />
          </div>
        )}
        <div className="divide-y divide-slate-800/70">
          <Fact
            label="Cores"
            value={`${info.cpu.physical_cores ?? '—'} physical · ${
              info.cpu.logical_cores ?? '—'
            } logical`}
          />
          <Fact label="Base clock" value={fmtMHz(info.cpu.base_clock_mhz)} />
          <Fact label="Architecture" value={info.cpu.architecture ?? '—'} />
          <Fact label="Identifier" value={info.cpu.identifier ?? '—'} />
        </div>
      </Card>

      <Card icon={MemoryStick} title="Memory">
        {ram.load_pct != null && (
          <div className="mb-3">
            <div className="flex items-baseline justify-between text-xs text-slate-400 mb-1">
              <span>In use</span>
              <span className="tabular-nums text-slate-200">
                {fmtBytes(ram.used)} / {fmtBytes(ram.total)} · {ram.load_pct.toFixed(0)} %
              </span>
            </div>
            <Bar pct={ram.load_pct} />
          </div>
        )}
        <div className="divide-y divide-slate-800/70">
          <Fact label="Installed" value={fmtBytes(ram.total)} />
          <Fact label="Available" value={fmtBytes(ram.available)} />
          <Fact
            label="Commit charge"
            value={`${fmtBytes(
              (ram.commit_total ?? 0) - (ram.commit_available ?? 0),
            )} / ${fmtBytes(ram.commit_total)}`}
            hint="Physical memory plus the page file — what the system has promised in total."
          />
        </div>
      </Card>

      {adapters.map((adapter) => (
        <GpuCard key={adapter.luid} adapter={adapter} live={byLuid.get(adapter.luid)} />
      ))}

      <Card
        icon={CircuitBoard}
        title="Vulkan devices"
        subtitle="spirula sam devices"
      >
        <p className="text-xs text-slate-500 mb-3 leading-snug">
          The reconstruction tool's own verdict on this machine, read off the installed binary.
          This app is Vulkan and not CUDA, so every device below is one it can actually run on —
          an integrated GPU listed <span className="text-emerald-400">ok</span> is the whole
          claim, not a footnote.
        </p>
        {info.spirula.devices.length === 0 ? (
          <p className="text-xs text-amber-400 flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
            {info.spirula.reason ?? 'spirula listed no device.'}
          </p>
        ) : (
          <table className="w-full text-xs">
            <thead className="text-slate-500">
              <tr className="text-left">
                <th className="font-normal pb-1 pr-3">#</th>
                <th className="font-normal pb-1 pr-3">Device</th>
                <th className="font-normal pb-1 pr-3">Type</th>
                <th className="font-normal pb-1 pr-3">VRAM</th>
                <th className="font-normal pb-1">Status</th>
              </tr>
            </thead>
            <tbody className="text-slate-300">
              {info.spirula.devices.map((d) => (
                <tr key={d.index} className="border-t border-slate-800/70">
                  <td className="py-1 pr-3 tabular-nums text-slate-500">{d.index}</td>
                  <td className="py-1 pr-3">{d.name}</td>
                  <td className="py-1 pr-3 text-slate-400">{d.type}</td>
                  <td className="py-1 pr-3 tabular-nums">{d.vram}</td>
                  <td className="py-1">
                    <span
                      className={`inline-flex items-center gap-1 ${
                        d.status.toLowerCase() === 'ok' ? 'text-emerald-400' : 'text-amber-400'
                      }`}
                    >
                      {d.status.toLowerCase() === 'ok' ? (
                        <Check className="w-3 h-3" />
                      ) : (
                        <X className="w-3 h-3" />
                      )}
                      {d.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card icon={Cpu} title="System">
        <div className="divide-y divide-slate-800/70">
          <Fact
            label="Operating system"
            value={`${info.platform.system ?? '—'} ${info.platform.release ?? ''} ${
              info.platform.version ?? ''
            }`.trim()}
          />
          <Fact label="Machine name" value={info.platform.node ?? '—'} />
          <Fact label="Python" value={info.platform.python ?? '—'} />
          <Fact
            label="GPU counters"
            value={
              info.gpu_counters.available ? (
                <span className="text-emerald-400">{info.gpu_counters.source}</span>
              ) : (
                <span className="text-amber-400">{info.gpu_counters.reason ?? 'unavailable'}</span>
              )
            }
            hint="Vendor-neutral: the same counter reports an NVIDIA, AMD or Intel adapter."
          />
        </div>
      </Card>
    </div>
  );
};

export default HardwareSection;
