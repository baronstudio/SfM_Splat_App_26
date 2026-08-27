import React from 'react';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import { Loader2, AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { usePipelineStore } from '@/store/pipelineStore';

/**
 * Blocking progress modal for the project file operations (copy, reset,
 * archive, restore).
 *
 * Modal on purpose, and mounted by the shell rather than by the project list:
 * a copy moves gigabytes, and while it ran the user could start a step, change
 * project or navigate away from the only tile showing that anything was
 * happening. There is no safe way to interrupt a half-copied directory either
 * — no process to kill, unlike a pipeline step — so the honest UI is one that
 * says "wait" and means it.
 *
 * It closes itself when the request returns. On failure it stays up with the
 * error, because a message that disappears on its own is a message nobody read.
 */
export const ProjectOperationDialog: React.FC = () => {
  const projectOp = usePipelineStore((s) => s.projectOp);
  const endProjectOp = usePipelineStore((s) => s.endProjectOp);

  if (!projectOp) return null;

  const { title, projectName, progress, message, error } = projectOp;
  const percent = Math.round(Math.min(Math.max(progress, 0), 1) * 100);

  return (
    <DialogPrimitive.Root open>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-[60] bg-black/70 backdrop-blur-sm" />
        <DialogPrimitive.Content
          // Nothing dismisses this but the operation itself: no Escape, no
          // click-outside, no close button.
          onEscapeKeyDown={(e) => e.preventDefault()}
          onPointerDownOutside={(e) => e.preventDefault()}
          onInteractOutside={(e) => e.preventDefault()}
          className="fixed left-1/2 top-1/2 z-[61] w-[min(28rem,90vw)] -translate-x-1/2 -translate-y-1/2
                     rounded-lg border border-slate-700 bg-slate-900 p-6 shadow-xl"
        >
          <DialogPrimitive.Title className="flex items-center gap-2 text-base font-semibold text-slate-100">
            {error
              ? <AlertTriangle className="w-4 h-4 text-red-400" />
              : <Loader2 className="w-4 h-4 text-cyan-400 animate-spin" />}
            {error ? `${title} failed` : title}
          </DialogPrimitive.Title>

          <DialogPrimitive.Description className="mt-1 text-xs text-slate-400 truncate">
            {projectName}
          </DialogPrimitive.Description>

          {error ? (
            <p className="mt-4 text-sm text-red-400 bg-red-950/30 border border-red-900 rounded px-3 py-2">
              {error}
            </p>
          ) : (
            <>
              <div className="mt-4 h-2 w-full rounded-full bg-slate-800 overflow-hidden">
                <div
                  className="h-2 rounded-full bg-cyan-500 transition-[width] duration-200"
                  style={{ width: `${percent}%` }}
                />
              </div>
              <div className="mt-2 flex items-baseline justify-between gap-3">
                <p className="text-xs text-slate-400 truncate">{message}</p>
                <span className="text-xs text-slate-500 shrink-0 tabular-nums">{percent}%</span>
              </div>
              <p className="mt-4 text-[11px] text-slate-500">
                Moving files — this cannot be interrupted. The window closes on its own.
              </p>
            </>
          )}

          {error && (
            <div className="mt-4 flex justify-end">
              <Button size="sm" variant="outline" className="h-7 text-xs" onClick={endProjectOp}>
                Close
              </Button>
            </div>
          )}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
};

export default ProjectOperationDialog;
