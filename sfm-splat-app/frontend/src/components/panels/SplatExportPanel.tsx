import React from 'react';
import {
  AlertTriangle, Bookmark, Download, Info, Package, Trash2,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { staticUrl } from '@/api/client';
import SaveState from '@/components/settings/SaveState';
import { EXPORT_FORMATS, type SplatExportTool } from '@/hooks/useSplatExport';
import type { SplatExportFormat } from '@/types';

/**
 * The splat export panel (CLAUDE.md §7.6c).
 *
 * The one thing this panel exists to make clear is that **nothing downstream
 * reads what it writes**. The crop panel one section up produces pipeline data
 * — steps 5 and 6 mesh the cut — whereas everything here lands in a drawer of
 * deliverables and stays there. So the wording throughout is "download", never
 * "apply", and the drawer is listed rather than summarised: a file nobody can
 * see is a file nobody will fetch.
 *
 * It is the last thing on step 4's page for the same reason. Everything above
 * it makes the splat — the run, then the crop that trims it inside the viewer —
 * and this is the only thing that makes a file for somebody else. Asking for an
 * export format before the scene has been seen or cut is the wrong order.
 *
 * Four things it says out loud rather than leaving to be discovered:
 *
 * * **Which file it is about to read.** `resolve_splat` prefers the crop, so an
 *   export taken after one carries it — but the live cut is a *shader test on
 *   the preview*, so a scene can look trimmed with nothing on disk cut at all.
 *   Volumes placed and not applied are the one confusion this feature can
 *   plausibly cause, and they get a warning here and a WARNING in the run.
 * * **What each reduction costs**, in the measured numbers rather than in
 *   adjectives. "SH 0 — 3.65x smaller, no view-dependent colour" is a decision
 *   somebody can take; "optimise" is not.
 * * **That the opacity floor is not free housekeeping here.** Spirula's
 *   gaussians are low-opacity by construction, so the 1/255 threshold every
 *   other toolchain ships drops ~1 % and anything higher is an edit.
 * * **That three formats need a tool that may not be installed**, with the
 *   install line in the panel — rather than a button that fails after the
 *   reduction has already run.
 */

function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024).toFixed(0)} KB`;
}

/** SH bands, with what each one actually costs on the reference splat. */
const SH_CHOICES: { value: number | null; label: string; hint: string }[] = [
  {
    value: null,
    label: 'Keep',
    hint: 'Every band the trainer wrote. The export is the splat, bit for bit.',
  },
  {
    value: 2,
    label: '2',
    hint: 'Drops the highest band — 24 of the 45 coefficients survive.',
  },
  {
    value: 1,
    label: '1',
    hint: '9 of 45 coefficients. Measured 2.38x smaller, and most of the '
      + 'view-dependent shading survives.',
  },
  {
    value: 0,
    label: '0',
    hint: 'No view-dependent colour at all. Measured 3.65x smaller — 72.6 % of '
      + 'every vertex is spherical harmonics.',
  },
];

const SELECTIONS: { value: 'importance' | 'uniform'; label: string; hint: string }[] = [
  {
    value: 'importance',
    label: 'By importance',
    hint: 'Keeps the highest opacity x volume. Approximate — the proper score '
      + 'counts how often each gaussian is actually hit, which needs a trainer.',
  },
  {
    value: 'uniform',
    label: 'Evenly',
    hint: 'An even spread over the file, never the first N — a PLY is not '
      + 'shuffled, so its first million points are one corner of the scene.',
  },
];

const Field: React.FC<{ label: string; hint?: string; children: React.ReactNode }> = ({
  label, hint, children,
}) => (
  <div className="space-y-1">
    <span className="text-xs text-slate-400 uppercase tracking-wide">{label}</span>
    {children}
    {hint && <p className="text-[11px] text-slate-500 leading-snug">{hint}</p>}
  </div>
);

const SplatExportPanel: React.FC<{ tool: SplatExportTool; disabled?: boolean }> = ({
  tool, disabled,
}) => {
  const { settings, setSettings, state } = tool;
  if (!settings) return null;

  const source = state?.source;
  const external = state?.formats.external ?? [];
  const toolMissing = !(state?.splat_transform.available ?? true);
  const chosen = EXPORT_FORMATS.find((f) => f.value === settings.format);
  const sourceDegree = source?.sh_degree ?? null;
  const crop = state?.crop;
  // The saved viewpoint (§7.6d), and where it would land in *this* format: the
  // PLY header carries it, everything else gets a sidecar beside the file.
  const view = state?.viewpoint;
  const viewInHeader = Boolean(
    view?.saved && view.valid
    && (view.header_formats ?? []).includes(settings.format),
  );
  // Volumes on the preview, nothing cut on disk: the scene looks trimmed and
  // the export would not be. The one state worth interrupting for.
  const cropPending = Boolean(crop && crop.volumes > 0 && !crop.applied);

  const set = (patch: Partial<typeof settings>) => setSettings({ ...settings, ...patch });

  return (
    <div className="rounded-lg bg-slate-800 border border-slate-700 p-4 space-y-4">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2">
          <Package className="w-4 h-4 text-cyan-400" />
          <h3 className="text-sm font-medium text-slate-200">Export the splat</h3>
        </div>
        <SaveState saving={tool.saving} savedAt={tool.savedAt} error={tool.saveError} />
      </div>

      {/* What it will read, before it reads it — the same contract every other
          panel in this app keeps. `resolve_splat` prefers the crop, so this
          line is also how the user learns whether their crop is in the export. */}
      <p className="text-xs text-slate-400">
        {source?.available ? (
          <>
            Reads{' '}
            <span className="font-mono text-slate-300">{source.file}</span>
            {source.cropped ? (
              <span className="text-cyan-400"> — the cropped splat</span>
            ) : (
              <span className="text-slate-500"> — the trained splat</span>
            )}
            {', '}
            {source.count?.toLocaleString()} gaussians,{' '}
            {formatBytes(source.bytes ?? 0)}, SH degree{' '}
            {sourceDegree ?? '?'} ({source.properties} properties).
          </>
        ) : (
          <span className="text-amber-400">
            No trained splat yet — run step 4 first.
          </span>
        )}
      </p>

      {/* The camera saved on the viewer above. It is the one part of this
          export that comes from somebody having *looked* at the scene, so it
          says where it will end up rather than only that it exists. */}
      {view?.saved && view.valid && (
        <p className="flex gap-2 text-xs text-slate-400">
          <Bookmark className="w-3.5 h-3.5 mt-0.5 shrink-0 text-cyan-500" />
          <span>
            The saved view travels with this export —{' '}
            {viewInHeader ? (
              <>in the PLY header, as <span className="font-mono">comment
                viewpoint …</span> lines every reader skips.</>
            ) : (
              <>beside the file, as{' '}
                <span className="font-mono">
                  &lt;name&gt;{view.sidecar_suffix}
                </span>
                : this format has nowhere to put it.</>
            )}
          </span>
        </p>
      )}

      {view?.saved && !view.valid && (
        <p className="flex gap-2 text-xs text-amber-300">
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span>
            The stored viewpoint will not parse and is left out of this export
            ({view.error}). Press <span className="font-medium">Save view</span>{' '}
            on the viewer above to replace it.
          </span>
        </p>
      )}

      {view && !view.saved && source?.available && (
        <p className="text-xs text-slate-500">
          No view saved. <span className="font-medium">Save view</span> on the
          viewer above stores the camera, and the export carries it — in the PLY
          header, or beside the file for the formats that cannot hold it.
        </p>
      )}

      {/* Volumes are drawn on the preview and nothing has been cut. The viewer
          above hides the excluded gaussians in its shader, so the scene already
          looks trimmed — and this export would be the whole splat. */}
      {cropPending && (
        <p className="flex gap-2 text-xs text-amber-300 bg-amber-950/20 border border-amber-900/60 rounded px-3 py-2">
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span>
            {crop?.volumes} crop volume{crop && crop.volumes > 1 ? 's are' : ' is'}{' '}
            placed but the crop has not been applied. The viewer hides the
            excluded gaussians live, so the scene looks cut — this export would
            not be. Press <span className="font-medium">Apply crop</span> above
            first if the cut belongs in it.
          </span>
        </p>
      )}

      {/* Cut, then dragged. The file is not wrong, it is old — and it is what
          steps 5 and 6 are reading too, which is the crop panel's warning. */}
      {crop?.stale && (
        <p className="flex gap-2 text-xs text-amber-300/90">
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span>
            The crop on disk is older than the volumes above it — this export
            carries the earlier cut. Re-apply the crop to bring it up to date.
          </span>
        </p>
      )}

      {/* ── Format ─────────────────────────────────────────────────────────── */}
      <Field label="Format" hint={chosen?.hint}>
        <div className="flex flex-wrap gap-1">
          {EXPORT_FORMATS.map((f) => {
            const needsTool = external.includes(f.value as SplatExportFormat);
            const off = needsTool && toolMissing;
            return (
              <button
                key={f.value}
                type="button"
                onClick={() => set({ format: f.value })}
                title={off ? 'Needs splat-transform — see below' : f.hint}
                className={`px-2.5 py-1 rounded text-xs border transition-colors ${
                  settings.format === f.value
                    ? 'bg-cyan-600 border-cyan-500 text-white'
                    : off
                      ? 'bg-slate-900 border-slate-700 text-slate-600'
                      : 'bg-slate-900 border-slate-700 text-slate-300 hover:border-slate-500'
                }`}
              >
                {f.label}
                {needsTool && <span className="ml-1 opacity-60">*</span>}
              </button>
            );
          })}
        </div>
      </Field>

      {toolMissing && (
        <p className="flex gap-2 text-xs text-amber-300 bg-amber-950/20 border border-amber-900/60 rounded px-3 py-2">
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span className="space-y-1">
            <span className="block">
              The three formats marked <span className="font-mono">*</span> are
              written by <span className="font-mono">@playcanvas/splat-transform</span>,
              which is not installed. PLY and .splat need nothing.
            </span>
            <span className="block font-mono text-[11px] text-amber-200/80">
              npm install --prefix tools/splat-transform @playcanvas/splat-transform
            </span>
          </span>
        </p>
      )}

      <div className="grid sm:grid-cols-2 gap-4">
        {/* ── Spherical harmonics ──────────────────────────────────────────── */}
        <Field
          label="Spherical harmonics"
          hint={SH_CHOICES.find((c) => c.value === settings.sh_degree)?.hint}
        >
          <div className="flex gap-1">
            {SH_CHOICES.map((c) => {
              // Asking for more bands than the file holds is a no-op, not an
              // error — but offering it as if it were a reduction is a lie.
              const noop = c.value !== null && sourceDegree !== null
                && c.value >= sourceDegree;
              return (
                <button
                  key={String(c.value)}
                  type="button"
                  onClick={() => set({ sh_degree: c.value })}
                  title={noop ? `This splat is already degree ${sourceDegree}` : c.hint}
                  className={`px-2.5 py-1 rounded text-xs border transition-colors ${
                    settings.sh_degree === c.value
                      ? 'bg-cyan-600 border-cyan-500 text-white'
                      : noop
                        ? 'bg-slate-900 border-slate-700 text-slate-600'
                        : 'bg-slate-900 border-slate-700 text-slate-300 hover:border-slate-500'
                  }`}
                >
                  {c.label}
                </button>
              );
            })}
          </div>
        </Field>

        {/* ── Opacity floor ────────────────────────────────────────────────── */}
        <Field
          label="Opacity floor"
          hint={settings.opacity_min > 0
            ? 'Every gaussian fainter than this is dropped. Spirula trains at '
              + 'low opacity on purpose, so this cuts deeper here than the same '
              + 'number would elsewhere: 0.05 removed 43 % of the reference splat.'
            : 'Off — every gaussian is kept. The usual 1/255 housekeeping '
              + 'threshold drops barely 1 % of a spirula splat, so it is not on '
              + 'by default.'}
        >
          <div className="flex items-center gap-2">
            <input
              type="range"
              min={0}
              max={0.2}
              step={0.005}
              value={settings.opacity_min}
              onChange={(e) => set({ opacity_min: Number(e.target.value) })}
              className="flex-1 accent-cyan-500"
            />
            <span className="text-xs font-mono text-slate-300 w-12 text-right">
              {settings.opacity_min > 0 ? settings.opacity_min.toFixed(3) : 'off'}
            </span>
          </div>
        </Field>

        {/* ── Target count ─────────────────────────────────────────────────── */}
        <Field
          label="Gaussian count"
          hint={settings.max_count > 0
            ? 'A hard cap on how many survive. Nothing is re-fitted afterwards, '
              + 'so a deep cut thins the picture rather than simplifying it.'
            : 'Off — keep every gaussian that clears the floor.'}
        >
          <input
            type="number"
            min={0}
            step={10000}
            value={settings.max_count}
            onChange={(e) => set({ max_count: Math.max(0, Number(e.target.value) || 0) })}
            placeholder="0 — no limit"
            className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1
                       text-xs text-slate-200 font-mono"
          />
        </Field>

        {settings.max_count > 0 && (
          <Field
            label="Which ones"
            hint={SELECTIONS.find((s) => s.value === settings.selection)?.hint}
          >
            <div className="flex gap-1">
              {SELECTIONS.map((s) => (
                <button
                  key={s.value}
                  type="button"
                  onClick={() => set({ selection: s.value })}
                  title={s.hint}
                  className={`px-2.5 py-1 rounded text-xs border transition-colors ${
                    settings.selection === s.value
                      ? 'bg-cyan-600 border-cyan-500 text-white'
                      : 'bg-slate-900 border-slate-700 text-slate-300 hover:border-slate-500'
                  }`}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </Field>
        )}
      </div>

      {/* ── Run ────────────────────────────────────────────────────────────── */}
      <div className="flex items-center gap-3 flex-wrap border-t border-slate-700/60 pt-4">
        <Button
          onClick={() => void tool.run()}
          disabled={disabled || tool.running || tool.blocked || !source?.available}
          className="bg-slate-700 hover:bg-slate-600 text-white gap-1"
        >
          <Package className="w-4 h-4" />
          {tool.running ? 'Exporting…' : 'Export'}
        </Button>
        <span className="text-xs text-slate-500">
          Writes into <span className="font-mono">train/export/</span>. Nothing
          in the pipeline reads it — the mesh step keeps using the trained
          splat.
        </span>
      </div>

      {tool.error && (
        <p className="text-xs text-red-400 bg-red-950/30 border border-red-800 rounded px-3 py-2">
          {tool.error}
        </p>
      )}

      {tool.state?.applied && (
        <p className="flex gap-2 text-xs text-slate-400">
          <Info className="w-3.5 h-3.5 mt-0.5 shrink-0 text-cyan-500" />
          <span>
            Last export:{' '}
            <span className="font-mono text-slate-300">
              {tool.state.applied.filename}
            </span>{' '}
            — {tool.state.applied.count.toLocaleString()} gaussians
            {tool.state.applied.removed > 0
              && `, ${tool.state.applied.removed.toLocaleString()} dropped`}
            {', '}{formatBytes(tool.state.applied.bytes)} in{' '}
            {tool.state.applied.seconds}s
            {tool.state.applied.splat_transform_version
              && ` · ${tool.state.applied.splat_transform_version}`}
            {tool.state.applied.viewpoint && (
              tool.state.applied.viewpoint_in_header
                ? ' · view in the header'
                : ` · view in ${tool.state.applied.viewpoint_sidecar}`
            )}
          </span>
        </p>
      )}

      {/* ── The drawer ─────────────────────────────────────────────────────── */}
      {(tool.state?.files.length ?? 0) > 0 && (
        <div className="space-y-1 border-t border-slate-700/60 pt-3">
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-400 uppercase tracking-wide">
              Exported files
            </span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void tool.clear()}
              disabled={disabled || tool.running}
              className="text-slate-500 hover:text-red-400 gap-1 text-xs h-7"
              title="Delete every exported file. Nothing downstream can notice — no step reads this directory."
            >
              <Trash2 className="w-3.5 h-3.5" />
              Clear
            </Button>
          </div>
          {tool.state?.files.map((f) => (
            <a
              key={f.filename}
              href={staticUrl(f.url)}
              download={f.filename}
              className="flex items-center justify-between gap-3 rounded bg-slate-900/60
                         border border-slate-700 px-3 py-1.5 hover:border-cyan-600
                         transition-colors group"
            >
              <span className="font-mono text-xs text-slate-300 truncate">
                {f.filename}
              </span>
              <span className="flex items-center gap-2 shrink-0">
                <span className="text-[11px] text-slate-500">
                  {formatBytes(f.bytes)}
                </span>
                <Download className="w-3.5 h-3.5 text-slate-500 group-hover:text-cyan-400" />
              </span>
            </a>
          ))}
        </div>
      )}
    </div>
  );
};

export default SplatExportPanel;
