import React from 'react';
import { Construction } from 'lucide-react';

/**
 * The placeholder steps 3 to 6 render until their step module exists.
 *
 * Deliberately inert: it starts nothing, and it names the tool, the spec section
 * and the TODO entry instead. CLAUDE.md §2.2 says every step calls its real tool
 * and there is no simulation layer, so a shell that offered a Run button would
 * be the first half of exactly the stub `3DGS_App_26` deleted on 2026-08-22.
 */
export interface StepShellProps {
  step: number;
  title: string;
  /** The command this step will drive, verbatim. */
  command: string;
  /** What it writes, relative to the project directory. */
  writes: string;
  /** CLAUDE.md section that specifies it. */
  spec: string;
  /** TODO.md entry that implements it. */
  todo: string;
  children?: React.ReactNode;
}

const StepShell: React.FC<StepShellProps> = ({
  step, title, command, writes, spec, todo, children,
}) => (
  <div className="mx-auto max-w-3xl space-y-6 p-6">
    <div className="flex items-start gap-3">
      <Construction className="mt-1 h-6 w-6 shrink-0 text-amber-500" />
      <div>
        <h2 className="text-xl font-semibold">
          Step {step} — {title}
        </h2>
        <p className="text-muted-foreground mt-1 text-sm">
          Not implemented yet. The tool is verified and the behaviour is
          specified; the step module is not written.
        </p>
      </div>
    </div>

    <dl className="grid grid-cols-[7rem_1fr] gap-x-4 gap-y-3 text-sm">
      <dt className="text-muted-foreground">Command</dt>
      <dd className="font-mono text-xs break-all">{command}</dd>

      <dt className="text-muted-foreground">Writes</dt>
      <dd className="font-mono text-xs">{writes}</dd>

      <dt className="text-muted-foreground">Specified in</dt>
      <dd>CLAUDE.md {spec}</dd>

      <dt className="text-muted-foreground">Implemented by</dt>
      <dd>TODO.md {todo}</dd>
    </dl>

    {children}
  </div>
);

export default StepShell;
