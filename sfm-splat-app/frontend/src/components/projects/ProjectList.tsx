import React from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { staticUrl } from '@/api/client';
import { useProjects } from '@/hooks/useProjects';
import { usePipelineStore } from '@/store/pipelineStore';
import {
  Plus, Trash2, PlayCircle, ImageOff, MoreVertical, Copy, RotateCcw,
  Archive, ArchiveRestore, Loader2, FolderOpen, Clock, PenLine, Video,
} from 'lucide-react';
import { absoluteDate, relativeDate } from '@/lib/dates';
import type { Project, StepStatus } from '@/types';
import { RESETTABLE_STEPS } from '@/types';

const STEP_LABELS: Record<number, string> = {
  0: 'Not started',
  1: 'Import',
  2: 'Extract',
  3: 'SfM',
  4: 'Training',
  5: 'Mesh',
};

const TOTAL_STEPS = 5;

function errorMessage(err: unknown): string {
  const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  return detail ?? (err instanceof Error ? err.message : String(err));
}

interface ProjectListProps {
  /** Called after a project is selected/created, to navigate to the wizard */
  onNavigate?: () => void;
  /**
   * Drop the Card chrome and the "New Project" button, for use inside a step
   * that already has its own panel and its own create form (step 1).
   */
  embedded?: boolean;
  /** Heading of the embedded variant. */
  heading?: string;
}

export const ProjectList: React.FC<ProjectListProps> = ({
  onNavigate,
  embedded = false,
  heading = 'Resume existing project',
}) => {
  const {
    projects, currentProjectId, setCurrentProject, setCurrentStep, hydrateFromProject,
    projectOp, startProjectOp, failProjectOp, endProjectOp,
  } = usePipelineStore();
  const {
    createProject, deleteProject, selectProject,
    copyProject, resetProject, archiveProject, unarchiveProject,
  } = useProjects();

  // Which project is mid-operation, for the tile itself. The blocking modal is
  // ProjectOperationDialog, driven from the store so it survives this list
  // unmounting when the user changes step.
  const busyId = projectOp?.projectId ?? null;

  const run = async (project: Project, title: string, action: () => Promise<unknown>) => {
    startProjectOp({ projectId: project.id, title, projectName: project.name });
    try {
      await action();
      endProjectOp();
    } catch (err) {
      // The modal stays up holding the error until it is dismissed — an alert()
      // behind a closing dialog is a message nobody reads.
      failProjectOp(errorMessage(err));
    }
  };

  const handleNew = () => {
    const name = prompt('Project name:');
    if (!name?.trim()) return;
    setCurrentProject(null);
    setCurrentStep(1);
    createProject(name.trim()).then(() => onNavigate?.());
  };

  const handleResume = (id: string, step: number) => {
    const project = projects.find((p) => p.id === id);
    selectProject(id);
    if (project) {
      hydrateFromProject(project);
    } else {
      setCurrentStep(Math.max(step, 1));
    }
    onNavigate?.();
  };

  const handleCopy = (p: Project) => {
    const name = prompt(`Name of the copy of "${p.name}":`, `${p.name} copy`);
    if (!name?.trim()) return;
    run(p, 'Copying project', () => copyProject(p.id, name.trim()));
  };

  const handleReset = (p: Project, fromStep: number | null) => {
    const scope = fromStep === null
      ? 'every step (2 → 6)'
      : `step ${fromStep} (${STEP_LABELS[fromStep]}) and everything after it`;
    if (!window.confirm(
      `Reset "${p.name}"?\n\n${scope} will be deleted from disk.\n`
      + 'The source video in input/ is kept.',
    )) return;
    run(p, 'Resetting project', async () => {
      const updated = await resetProject(p.id, fromStep === null ? undefined : [fromStep]);
      // The wizard is showing the state we just deleted — rewind it too.
      if (currentProjectId === p.id) hydrateFromProject(updated);
    });
  };

  const handleArchive = (p: Project) => {
    if (!window.confirm(
      `Archive "${p.name}"?\n\nThe project is compressed to a .zip and stays in `
      + 'this list, disabled, until you restore it. This can take a while on a '
      + 'trained project.',
    )) return;
    run(p, 'Archiving project', async () => {
      await archiveProject(p.id);
      // Nothing left on disk to work on: drop the selection rather than leave
      // the wizard pointing at an empty directory.
      if (currentProjectId === p.id) setCurrentProject(null);
    });
  };

  const handleDelete = (p: Project) => {
    if (!window.confirm(
      `Delete project "${p.name}"?\n\n${p.path}\n\nThis cannot be undone.`,
    )) return;
    run(p, 'Deleting project', () => deleteProject(p.id));
  };

  const body = (
    <>
        {projects.length === 0 ? (
          <p className="text-sm text-slate-500 text-center py-6">
            No projects yet. Start by importing a video.
          </p>
        ) : (
          <ul className="space-y-2">
            {projects.map((p) => {
              const busy = busyId === p.id;
              const disabled = p.archived || busy;
              return (
                <li
                  key={p.id}
                  className={`flex items-center gap-3 rounded-md bg-slate-900/60 px-3 py-2 border border-slate-700 ${
                    p.archived ? 'opacity-60' : ''
                  }`}
                >
                  {/* Thumbnail */}
                  <div className="w-12 h-12 shrink-0 rounded overflow-hidden bg-slate-800 border border-slate-700 flex items-center justify-center">
                    {busy ? (
                      <Loader2 className="w-5 h-5 text-cyan-400 animate-spin" />
                    ) : p.archived ? (
                      <Archive className="w-5 h-5 text-slate-500" />
                    ) : p.thumbnail_url ? (
                      <img
                        src={staticUrl(p.thumbnail_url)}
                        alt="thumbnail"
                        className="w-full h-full object-cover"
                      />
                    ) : (
                      <ImageOff className="w-5 h-5 text-slate-600" />
                    )}
                  </div>

                  {/* Info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-medium text-slate-100 truncate min-w-0">{p.name}</p>
                      {/* Footage author — the one piece of project info on the
                          tile, on the name's own line. The rest is in the
                          wizard's Project info panel. */}
                      {p.footage_author && (
                        <span
                          className="flex items-center gap-1 min-w-0 text-[11px] text-slate-400 truncate"
                          title={`Footage author: ${p.footage_author}`}
                        >
                          <Video className="w-3 h-3 shrink-0" />
                          <span className="truncate">{p.footage_author}</span>
                        </span>
                      )}
                      {p.archived && (
                        <Badge variant="outline" className="text-[10px] px-1 py-0 text-amber-400 border-amber-700/60">
                          Archived
                        </Badge>
                      )}
                    </div>

                    {/* Where the data actually is — full path on hover */}
                    <p
                      className="text-[11px] font-mono text-slate-500 truncate flex items-center gap-1"
                      title={p.archived ? (p.archive_path ?? p.path) : p.path}
                    >
                      <FolderOpen className="w-3 h-3 shrink-0" />
                      {p.archived ? (p.archive_path ?? p.path) : p.path}
                    </p>

                    {/* Created / last updated */}
                    <p className="text-[11px] text-slate-500 flex items-center gap-3">
                      <span className="flex items-center gap-1" title={absoluteDate(p.created_at)}>
                        <Clock className="w-3 h-3" />
                        Created {absoluteDate(p.created_at)}
                      </span>
                      <span className="flex items-center gap-1" title={absoluteDate(p.updated_at)}>
                        <PenLine className="w-3 h-3" />
                        Updated {relativeDate(p.updated_at)}
                      </span>
                    </p>

                    {/* Step progress */}
                    <div className="flex items-center gap-1.5 mt-1">
                      <div className="flex gap-0.5">
                        {Array.from({ length: TOTAL_STEPS }, (_, i) => {
                          const stepNum = i + 1;
                          const s = (p.step_status as Record<string, StepStatus>)?.[String(stepNum)];
                          return (
                            <div
                              key={stepNum}
                              className={`h-1.5 w-4 rounded-full ${
                                s === 'done' ? 'bg-green-500' :
                                s === 'running' ? 'bg-cyan-400 animate-pulse' :
                                s === 'error' ? 'bg-red-500' :
                                stepNum <= p.current_step ? 'bg-slate-400' : 'bg-slate-700'
                              }`}
                            />
                          );
                        })}
                      </div>
                      <span className="text-xs text-slate-500">
                        {p.current_step > 0 ? `${p.current_step}/${TOTAL_STEPS}` : '–'}
                      </span>
                    </div>
                  </div>

                  <Badge variant="outline" className="text-xs text-slate-400 border-slate-600 shrink-0">
                    {STEP_LABELS[p.current_step] ?? `Step ${p.current_step}`}
                  </Badge>

                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={disabled}
                    className="h-7 px-2 text-xs text-blue-400 hover:text-blue-300 disabled:opacity-40"
                    onClick={() => handleResume(p.id, p.current_step)}
                  >
                    <PlayCircle className="w-3 h-3 mr-1" />
                    Resume
                  </Button>

                  {/* Project options */}
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={busy}
                        className="h-7 px-1 text-slate-400 hover:text-slate-100"
                        aria-label={`Options for ${p.name}`}
                      >
                        {busy
                          ? <Loader2 className="w-4 h-4 animate-spin" />
                          : <MoreVertical className="w-4 h-4" />}
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-60 bg-slate-800 border-slate-700 text-slate-100">
                      <DropdownMenuLabel className="text-xs text-slate-400 truncate">
                        {p.name}
                      </DropdownMenuLabel>
                      <DropdownMenuSeparator className="bg-slate-700" />

                      {p.archived ? (
                        <DropdownMenuItem
                          className="cursor-pointer hover:bg-slate-700 gap-2"
                          onClick={() => run(p, 'Restoring project', () => unarchiveProject(p.id))}
                        >
                          <ArchiveRestore className="w-3.5 h-3.5" />
                          Restore from archive
                        </DropdownMenuItem>
                      ) : (
                        <>
                          <DropdownMenuItem
                            className="cursor-pointer hover:bg-slate-700 gap-2"
                            onClick={() => handleCopy(p)}
                          >
                            <Copy className="w-3.5 h-3.5" />
                            Copy project…
                          </DropdownMenuItem>

                          <DropdownMenuSub>
                            <DropdownMenuSubTrigger className="cursor-pointer hover:bg-slate-700 gap-2">
                              <RotateCcw className="w-3.5 h-3.5" />
                              Reset
                            </DropdownMenuSubTrigger>
                            <DropdownMenuSubContent className="w-64 bg-slate-800 border-slate-700 text-slate-100">
                              <DropdownMenuLabel className="text-[11px] font-normal text-slate-400">
                                The source video is always kept.
                              </DropdownMenuLabel>
                              <DropdownMenuSeparator className="bg-slate-700" />
                              <DropdownMenuItem
                                className="cursor-pointer hover:bg-slate-700"
                                onClick={() => handleReset(p, null)}
                              >
                                Whole project (steps 2 → 6)
                              </DropdownMenuItem>
                              <DropdownMenuSeparator className="bg-slate-700" />
                              {RESETTABLE_STEPS.map((step) => (
                                <DropdownMenuItem
                                  key={step}
                                  className="cursor-pointer hover:bg-slate-700"
                                  onClick={() => handleReset(p, step)}
                                >
                                  From step {step} — {STEP_LABELS[step]}
                                </DropdownMenuItem>
                              ))}
                            </DropdownMenuSubContent>
                          </DropdownMenuSub>

                          <DropdownMenuItem
                            className="cursor-pointer hover:bg-slate-700 gap-2"
                            onClick={() => handleArchive(p)}
                          >
                            <Archive className="w-3.5 h-3.5" />
                            Archive (zip)
                          </DropdownMenuItem>
                        </>
                      )}

                      <DropdownMenuSeparator className="bg-slate-700" />
                      <DropdownMenuItem
                        className="cursor-pointer hover:bg-slate-700 gap-2 text-red-400 focus:text-red-400"
                        onClick={() => handleDelete(p)}
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                        Delete project
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </li>
              );
            })}
          </ul>
        )}
    </>
  );

  if (embedded) {
    return (
      <div className="flex flex-col gap-2">
        <p className="text-xs font-medium text-slate-400 uppercase tracking-wide">{heading}</p>
        {body}
      </div>
    );
  }

  return (
    <Card className="bg-slate-800 border-slate-700">
      <CardHeader className="flex flex-row items-center justify-between py-3 px-4">
        <CardTitle className="text-base font-semibold text-slate-100">Projects</CardTitle>
        <Button size="sm" variant="outline" className="gap-1 h-7 text-xs" onClick={handleNew}>
          <Plus className="w-3 h-3" />
          New Project
        </Button>
      </CardHeader>
      <CardContent className="px-4 pb-4">{body}</CardContent>
    </Card>
  );
};

export default ProjectList;
