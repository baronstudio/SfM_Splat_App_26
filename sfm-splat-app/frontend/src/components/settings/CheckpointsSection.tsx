import React, { useMemo, useState } from 'react';
import {
  AlertTriangle,
  Check,
  CircleSlash,
  Download,
  ExternalLink,
  FolderOpen,
  HardDrive,
  RefreshCw,
  ShieldCheck,
  Trash2,
  Upload,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { useModels, type LicenceRow, type ModelRow } from '@/hooks/useModels';

/**
 * Checkpoints — the post-installation step this app used to leave to the user
 * (CLAUDE.md §7.4, §7.5, §10).
 *
 * It belongs in the **global setup panel**, beside the tool paths, and not on a
 * wizard step: a checkpoint is a property of this machine, like where FFmpeg
 * is, and asking for it from inside step 3 would ask the same question once per
 * project. The step panels keep their own `--model` fields; this is where the
 * file they name comes from.
 *
 * Two things it refuses to smooth over, because §10 says so:
 *
 * * **Four licences, accepted separately.** SAM 2.1 is Apache-2.0, SAM 3 is
 *   Meta's own; MoGe-2's mirrors and the Metric3D conversions are two more
 *   questions again. The accept control is per licence and the Download button
 *   does not exist until the matching one is on — and the backend re-checks it,
 *   so a licence cannot be skipped by calling the route directly.
 * * **The unaudited one says so.** §13.5 is still open for the MoGe mirrors and
 *   the row carries an amber note rather than a green tick.
 */

const fmtBytes = (n: number): string => {
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)} GB`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(0)} MB`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)} kB`;
  return `${n} B`;
};

const fmtDuration = (s: number): string => {
  if (!Number.isFinite(s) || s < 0) return '—';
  const m = Math.floor(s / 60);
  return m > 0 ? `${m}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`;
};

const STATE_STYLE: Record<string, { label: string; className: string }> = {
  ready: { label: 'Installed', className: 'text-emerald-400 border-emerald-800 bg-emerald-950/40' },
  missing: { label: 'Not installed', className: 'text-slate-500 border-slate-700 bg-slate-900' },
  partial: { label: 'Unfinished', className: 'text-amber-400 border-amber-800 bg-amber-950/40' },
  damaged: { label: 'Wrong size', className: 'text-red-400 border-red-900 bg-red-950/40' },
  downloading: { label: 'Downloading', className: 'text-cyan-400 border-cyan-800 bg-cyan-950/40' },
  verifying: { label: 'Verifying', className: 'text-cyan-400 border-cyan-800 bg-cyan-950/40' },
};

const Badge: React.FC<{ state: string }> = ({ state }) => {
  const s = STATE_STYLE[state] ?? STATE_STYLE.missing;
  return (
    <span className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded border ${s.className}`}>
      {s.label}
    </span>
  );
};

/* ── One checkpoint ─────────────────────────────────────────────────────── */

interface RowProps {
  model: ModelRow;
  licence: LicenceRow;
  accepted: boolean;
  inUse: boolean;
  busy: boolean;
  onDownload: () => void;
  onCancel: () => void;
  onVerify: () => void;
  onRemove: () => void;
  onUse: () => void;
  onAdopt: (path: string) => void;
}

const ModelCard: React.FC<RowProps> = ({
  model, licence, accepted, inUse, busy,
  onDownload, onCancel, onVerify, onRemove, onUse, onAdopt,
}) => {
  const [manual, setManual] = useState('');
  const [showManual, setShowManual] = useState(false);
  const job = model.job;
  const live = model.state === 'downloading' || model.state === 'verifying';
  const partBytes = model.files.reduce((a, f) => a + f.part_bytes, 0);

  return (
    <div className="py-3 first:pt-0">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-slate-100 text-sm font-medium">{model.label}</span>
            <Badge state={model.state} />
            {model.recommended && (
              <span className="text-[10px] uppercase tracking-wide text-cyan-400">recommended</span>
            )}
            {inUse && (
              <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded border border-cyan-800 bg-cyan-950/40 text-cyan-300">
                in use
              </span>
            )}
          </div>
          <p className="text-xs text-slate-500 mt-1 leading-snug max-w-prose">{model.blurb}</p>
          <p className="text-[11px] text-slate-600 mt-1 font-mono break-all">
            {model.filename}
            {model.extras.map((e) => ` + ${e.filename}`)}
            {' · '}
            {fmtBytes(model.total_bytes)}
            {model.sha256 ? ' · sha256 checked' : ' · size checked'}
          </p>
        </div>

        <div className="shrink-0 flex items-center gap-2">
          {live ? (
            <Button
              size="sm"
              variant="outline"
              onClick={onCancel}
              className="border-slate-600 text-slate-300 hover:text-slate-100 hover:bg-slate-800 gap-1"
            >
              <X className="w-3.5 h-3.5" />
              Cancel
            </Button>
          ) : model.state === 'ready' ? (
            <>
              <Button
                size="sm"
                variant="outline"
                disabled={busy || inUse}
                onClick={onUse}
                className="border-slate-600 text-slate-300 hover:text-slate-100 hover:bg-slate-800 gap-1"
              >
                <Check className="w-3.5 h-3.5" />
                {inUse ? 'In use' : `Use for ${model.family === 'sam' ? 'masking' : 'geometry'}`}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={onVerify}
                title="Re-read the file and check it against spirula's own manifest"
                className="border-slate-600 text-slate-400 hover:text-slate-100 hover:bg-slate-800"
              >
                <ShieldCheck className="w-3.5 h-3.5" />
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={onRemove}
                className="border-slate-700 text-slate-500 hover:text-red-300 hover:bg-slate-800"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </Button>
            </>
          ) : (
            <>
              <Button
                size="sm"
                disabled={!accepted || busy}
                onClick={onDownload}
                title={
                  accepted
                    ? undefined
                    : `Accept ${licence.name} first — it is shown above this family.`
                }
                className="bg-cyan-600 hover:bg-cyan-500 text-white gap-1 disabled:opacity-40"
              >
                <Download className="w-3.5 h-3.5" />
                {model.state === 'partial' ? 'Resume' : 'Download'}
              </Button>
              {(model.state === 'partial' || model.state === 'damaged') && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  onClick={onRemove}
                  title="Delete what is on disk, including the part"
                  className="border-slate-700 text-slate-500 hover:text-red-300 hover:bg-slate-800"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </Button>
              )}
            </>
          )}
        </div>
      </div>

      {/* The bar. Real bytes against a real total — both come off the manifest
          and a live HEAD, so it never guesses. */}
      {live && job && (
        <div className="mt-2">
          <div className="h-1.5 rounded bg-slate-800 overflow-hidden">
            <div
              className="h-full bg-cyan-500 transition-[width] duration-300"
              style={{ width: `${Math.round(job.progress * 100)}%` }}
            />
          </div>
          <p className="text-[11px] text-slate-500 mt-1">
            {fmtBytes(job.downloaded)} of {fmtBytes(job.total)} ·{' '}
            {job.rate_bps ? `${fmtBytes(job.rate_bps)}/s` : '—'}
            {job.eta_s != null && ` · ${fmtDuration(job.eta_s)} left`}
          </p>
        </div>
      )}

      {!live && model.state === 'partial' && partBytes > 0 && (
        <p className="text-[11px] text-amber-500/80 mt-1.5">
          {fmtBytes(partBytes)} already fetched and kept as a .part — Resume continues from
          there rather than starting again.
        </p>
      )}
      {!live && model.state === 'damaged' && (
        <p className="text-[11px] text-red-400/90 mt-1.5">
          A file of this name is installed but is not the right length. Delete it and download
          again — a truncated checkpoint that loads is worse than one that is missing.
        </p>
      )}

      {/* Manual install: the other half of the ask. The app runs on the machine
          that holds the file, so a checkpoint already downloaded by hand is a
          local path, never an upload (§6.7's argument). It is verified against
          the manifest before it is installed. */}
      {model.state !== 'ready' && !live && (
        <div className="mt-1.5">
          <button
            type="button"
            onClick={() => setShowManual((v) => !v)}
            className="text-[11px] text-slate-500 hover:text-slate-300 underline underline-offset-2"
          >
            {showManual ? 'Hide manual install' : 'I already downloaded this file'}
          </button>
          {showManual && (
            <div className="mt-2 space-y-2">
              <p className="text-[11px] text-slate-500 leading-snug">
                Give the path of the file on this machine. It is checked against{' '}
                {model.sha256 ? "spirula's own sha256" : 'the published byte count'} and then
                installed into the cache under the name the tool looks up. Or fetch it
                yourself from{' '}
                <a
                  href={model.url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-cyan-500 hover:text-cyan-400 inline-flex items-center gap-0.5"
                >
                  its source <ExternalLink className="w-3 h-3" />
                </a>
                .
              </p>
              <div className="flex gap-2">
                <Input
                  value={manual}
                  spellCheck={false}
                  placeholder={`C:\\Downloads\\${model.filename}`}
                  onChange={(e) => setManual(e.target.value)}
                  className="bg-slate-950 border-slate-700 text-slate-100 font-mono text-xs"
                />
                <Button
                  size="sm"
                  disabled={!manual.trim() || busy}
                  onClick={() => onAdopt(manual.trim())}
                  className="bg-slate-700 hover:bg-slate-600 text-slate-100 gap-1 shrink-0"
                >
                  <Upload className="w-3.5 h-3.5" />
                  Install
                </Button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

/* ── The section ────────────────────────────────────────────────────────── */

const FAMILIES: { id: 'sam' | 'geometry'; title: string; note: string }[] = [
  {
    id: 'sam',
    title: 'Masking — spirula sam',
    note:
      '`sam track --model` takes a file and never fetches one itself, so this is the only way a tracked mask gets a network. The lens-border mode needs none of this: it masks a shape, not an object, and runs with no model and no download.',
  },
  {
    id: 'geometry',
    title: 'Depth & normals — spirula geometry',
    note:
      'Installed here, the geometry pass never opens the network mid-run: it is handed the absolute path of the file instead of an id to fetch. Left uninstalled, spirula downloads MoGe-2 ViT-B (419 MB) through a curl child on the first pass.',
  },
];

const CheckpointsSection: React.FC<{ active: boolean }> = ({ active }) => {
  const models = useModels(active);
  const { overview, inUse, error, busy } = models;
  // Which licences the user has read and accepted, this session. Not persisted:
  // an acceptance is an act, and a stored one is a checkbox somebody ticked
  // once for a different checkpoint.
  const [accepted, setAccepted] = useState<Record<string, boolean>>({});
  const [note, setNote] = useState<string | null>(null);

  const byFamily = useMemo(() => {
    const out: Record<string, ModelRow[]> = { sam: [], geometry: [] };
    for (const m of overview?.models ?? []) out[m.family]?.push(m);
    return out;
  }, [overview]);

  if (!overview) {
    return <p className="text-sm text-slate-500">Reading the checkpoint cache…</p>;
  }

  const act = async (p: Promise<unknown>, message: string) => {
    const result = await p;
    if (result) setNote(message);
  };

  return (
    <div className="space-y-6">
      <div>
        <p className="text-xs text-slate-500 leading-snug max-w-prose">
          spirula.exe ships without a single neural checkpoint — 119 MB of tools and no
          weights. These are the twelve it knows about, with the file names, URLs and
          hashes read out of the installed binary itself, so a file installed here is
          indistinguishable from one the tool fetched for itself.
        </p>
      </div>

      {/* Where they go, and how much room is left. */}
      <div className="rounded border border-slate-800 bg-slate-900/50 p-3 space-y-1.5">
        <div className="flex items-center gap-2 text-xs text-slate-400">
          <FolderOpen className="w-3.5 h-3.5 shrink-0" />
          <span className="font-mono text-[11px] break-all text-slate-300">
            {overview.cache_dir}
          </span>
        </div>
        <div className="flex items-center gap-2 text-[11px] text-slate-500">
          <HardDrive className="w-3.5 h-3.5 shrink-0" />
          {fmtBytes(overview.cache_bytes)} of checkpoints
          {overview.disk_free_bytes != null &&
            ` · ${fmtBytes(overview.disk_free_bytes)} free on this disk`}
          {!overview.cache_dir_exists && ' · created on the first download'}
        </div>
        {overview.is_spirula_default ? (
          <p className="text-[11px] text-slate-600 leading-snug">
            This is spirula's own model directory, which is the one place a file installed
            here and a file fetched by the tool are the same file. Change it under Tools.
          </p>
        ) : (
          <p className="text-[11px] text-amber-500/80 leading-snug">
            Not spirula's own directory ({overview.spirula_default_cache}). No environment
            variable moves the tool's automatic fetch, so anything it downloads by itself
            still lands there — everything installed here is passed to it as an absolute
            path instead, which works either way.
          </p>
        )}
        {overview.unmanaged.length > 0 && (
          <p className="text-[11px] text-slate-600 leading-snug">
            Also in this directory and not from this list:{' '}
            {overview.unmanaged.map((u) => `${u.filename} (${fmtBytes(u.bytes)})`).join(', ')}.
          </p>
        )}
      </div>

      {error && (
        <div className="rounded border border-red-900 bg-red-950/40 p-2.5 text-xs text-red-300 flex gap-2">
          <CircleSlash className="w-4 h-4 shrink-0 mt-px" />
          <span className="leading-snug">{error}</span>
        </div>
      )}
      {note && !error && (
        <div className="rounded border border-emerald-900 bg-emerald-950/30 p-2.5 text-xs text-emerald-300 flex gap-2">
          <Check className="w-4 h-4 shrink-0 mt-px" />
          <span className="leading-snug">{note}</span>
        </div>
      )}

      {FAMILIES.map((family) => {
        const rows = byFamily[family.id] ?? [];
        const licenceIds = [...new Set(rows.map((r) => r.licence))];
        return (
          <div key={family.id}>
            <p className="text-xs font-semibold uppercase tracking-wide text-cyan-400">
              {family.title}
            </p>
            <p className="text-xs text-slate-500 mt-1 leading-snug max-w-prose">{family.note}</p>

            {/* One accept control per licence, never one for the family (§10). */}
            <div className="mt-3 space-y-2">
              {licenceIds.map((id) => {
                const licence = overview.licences[id];
                if (!licence) return null;
                return (
                  <div
                    key={id}
                    className="rounded border border-slate-800 bg-slate-900/40 p-2.5 flex items-start gap-3"
                  >
                    <Switch
                      checked={!!accepted[id]}
                      onCheckedChange={(v) => setAccepted((a) => ({ ...a, [id]: v }))}
                      className="mt-0.5 shrink-0"
                    />
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-xs text-slate-200">{licence.name}</span>
                        <a
                          href={licence.url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-[11px] text-cyan-500 hover:text-cyan-400 inline-flex items-center gap-0.5"
                        >
                          read it <ExternalLink className="w-3 h-3" />
                        </a>
                        {!licence.audited && (
                          <span className="text-[10px] uppercase tracking-wide text-amber-400 inline-flex items-center gap-1">
                            <AlertTriangle className="w-3 h-3" /> not audited
                          </span>
                        )}
                      </div>
                      <p className="text-[11px] text-slate-500 mt-0.5 leading-snug max-w-prose">
                        {licence.summary}
                      </p>
                    </div>
                  </div>
                );
              })}
            </div>

            <div className="mt-2 divide-y divide-slate-800">
              {rows.map((model) => (
                <ModelCard
                  key={model.id}
                  model={model}
                  licence={overview.licences[model.licence]}
                  accepted={!!accepted[model.licence]}
                  inUse={inUse?.[model.family]?.model_id === model.id}
                  busy={busy !== null}
                  onDownload={() => void models.download(model.id, model.licence)}
                  onCancel={() => void models.cancel(model.id)}
                  onVerify={() =>
                    void models.verify(model.id).then((r: any) => {
                      if (r) {
                        setNote(
                          r.ok
                            ? `${model.label}: ${r.files.map((f: any) => f.reason).join('; ')}`
                            : `${model.label} FAILED: ${r.files
                                .filter((f: any) => !f.ok)
                                .map((f: any) => `${f.filename} — ${f.reason}`)
                                .join('; ')}`,
                        );
                      }
                    })
                  }
                  onRemove={() =>
                    void act(models.remove(model.id), `${model.label} removed from the cache.`)
                  }
                  onUse={() =>
                    void act(
                      models.use(model.id),
                      `${model.label} is now the default checkpoint for ${
                        model.family === 'sam' ? 'masking' : 'geometry'
                      }. A project can still override it.`,
                    )
                  }
                  onAdopt={(path) =>
                    void act(models.adopt(model.id, path), `${model.label} installed from ${path}.`)
                  }
                />
              ))}
            </div>
          </div>
        );
      })}

      <div className="flex justify-end">
        <Button
          size="sm"
          variant="outline"
          onClick={() => void models.refresh()}
          className="border-slate-600 text-slate-400 hover:text-slate-100 hover:bg-slate-800 gap-1"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          Re-read the cache
        </Button>
      </div>
    </div>
  );
};

export default CheckpointsSection;
