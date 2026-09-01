import React, { useState } from 'react';
import { ChevronDown, ChevronRight, Info } from 'lucide-react';
import { usePipelineStore } from '@/store/pipelineStore';
import { useProjectInfo } from '@/hooks/useProjectInfo';
import SaveState from '@/components/settings/SaveState';
import { absoluteDate, relativeDate } from '@/lib/dates';

const STEP_LABELS: Record<number, string> = {
  0: 'Not started',
  1: 'Import',
  2: 'Extract',
  3: 'SfM',
  4: 'Training',
  5: 'Mesh',
};

const TOTAL_STEPS = 5;

/** One read-only row of the recap: a label over its value. */
const Row: React.FC<{ label: string; value?: string | null; mono?: boolean; title?: string }> = ({
  label, value, mono = false, title,
}) => (
  <div className="flex flex-col gap-0.5">
    <span className="text-[10px] uppercase tracking-wide text-slate-500">{label}</span>
    <span
      className={`text-[11px] text-slate-300 break-words ${mono ? 'font-mono' : ''}`}
      title={title ?? value ?? undefined}
    >
      {value || <span className="text-slate-600">—</span>}
    </span>
  </div>
);

/**
 * "Project info" — the recap of the open project, under the step navigator.
 *
 * Two of its fields are the user's own free text (footage author, description)
 * and are edited here rather than on a wizard step: they describe the project,
 * not a step of it, and they are the same two whatever step is open. They save
 * on every change like every other per-project panel (CLAUDE.md §4) — the
 * footage author is what the project list draws beside the name.
 */
export const ProjectInfoPanel: React.FC = () => {
  const { currentProjectId, projects } = usePipelineStore();
  const project = projects.find((p) => p.id === currentProjectId) ?? null;
  const [open, setOpen] = useState(false);
  const { fields, setField, saving, savedAt, error } = useProjectInfo(project);

  const disabled = !project || project.archived;

  return (
    <div className="border-t border-slate-700 mt-2 pt-2">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-slate-300 hover:text-slate-100 hover:bg-slate-800/50 rounded-r-md"
        aria-expanded={open}
      >
        <Info className="w-4 h-4 shrink-0" />
        <span className="flex-1 truncate">Project info</span>
        {open
          ? <ChevronDown className="w-4 h-4 shrink-0 text-slate-500" />
          : <ChevronRight className="w-4 h-4 shrink-0 text-slate-500" />}
      </button>

      {open && (
        <div className="px-3 pb-3 pt-1 flex flex-col gap-3">
          {!project ? (
            <p className="text-[11px] text-slate-500">No project selected.</p>
          ) : (
            <>
              {/* Editable — the two free-text fields */}
              <div className="flex flex-col gap-1">
                <label
                  htmlFor="project-footage-author"
                  className="text-[10px] uppercase tracking-wide text-slate-500"
                >
                  Footage author
                </label>
                <input
                  id="project-footage-author"
                  value={fields.footage_author}
                  disabled={disabled}
                  onChange={(e) => setField('footage_author', e.target.value)}
                  placeholder="Who shot it"
                  className="w-full rounded-md border border-slate-700 bg-slate-800 px-2 py-1 text-[11px] text-slate-200 placeholder:text-slate-600 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-cyan-400 disabled:opacity-50"
                />
              </div>

              <div className="flex flex-col gap-1">
                <label
                  htmlFor="project-description"
                  className="text-[10px] uppercase tracking-wide text-slate-500"
                >
                  Description
                </label>
                <textarea
                  id="project-description"
                  value={fields.description}
                  disabled={disabled}
                  rows={4}
                  onChange={(e) => setField('description', e.target.value)}
                  placeholder="What this project is"
                  className="w-full resize-y rounded-md border border-slate-700 bg-slate-800 px-2 py-1 text-[11px] text-slate-200 placeholder:text-slate-600 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-cyan-400 disabled:opacity-50"
                />
              </div>

              <div className="min-h-[16px]">
                <SaveState saving={saving} savedAt={savedAt} error={error} label="Saved" />
              </div>

              {/* Recap — everything else the row knows */}
              <div className="flex flex-col gap-2 border-t border-slate-700/60 pt-2">
                <Row label="Name" value={project.name} />
                <Row
                  label="Step"
                  value={
                    project.current_step > 0
                      ? `${project.current_step}/${TOTAL_STEPS} — ${STEP_LABELS[project.current_step] ?? ''}`
                      : STEP_LABELS[0]
                  }
                />
                <Row label="Frames" value={project.frame_count ? String(project.frame_count) : '0'} />
                <Row
                  label="Source"
                  value={project.input_video_path?.split(/[\/]/).pop() ?? null}
                  title={project.input_video_path ?? undefined}
                  mono
                />
                <Row
                  label={project.archived ? 'Archive' : 'Folder'}
                  value={project.archived ? (project.archive_path ?? project.path) : project.path}
                  mono
                />
                <Row label="Created" value={absoluteDate(project.created_at)} />
                <Row
                  label="Updated"
                  value={relativeDate(project.updated_at)}
                  title={absoluteDate(project.updated_at)}
                />
                {project.archived && (
                  <Row label="Archived" value={absoluteDate(project.archived_at ?? project.updated_at)} />
                )}
                {project.error_message && (
                  <div className="flex flex-col gap-0.5">
                    <span className="text-[10px] uppercase tracking-wide text-slate-500">Last error</span>
                    <span className="text-[11px] text-red-400 break-words">{project.error_message}</span>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
};

export default ProjectInfoPanel;
