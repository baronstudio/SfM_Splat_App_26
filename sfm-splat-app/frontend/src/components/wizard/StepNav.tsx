import React from 'react';
import { Upload, Film, Crosshair, Cpu, Package, Box, CheckCircle, XCircle } from 'lucide-react';
import { usePipelineStore } from '@/store/pipelineStore';
import type { StepStatus } from '@/types';

const STEPS = [
  { index: 1, icon: Upload, label: 'Import', key: 'import' },
  { index: 2, icon: Film, label: 'Extract Frames', key: 'extract' },
  { index: 3, icon: Crosshair, label: 'SfM', key: 'sfm' },
  { index: 4, icon: Cpu, label: 'Training', key: 'train' },
  { index: 5, icon: Package, label: 'Mesh', key: 'mesh' },
  { index: 6, icon: Box, label: 'Blender Scene', key: 'blender' },
] as const;

function StatusBadge({ status }: { status: StepStatus }) {
  if (status === 'done') {
    return <CheckCircle className="w-4 h-4 text-green-400 shrink-0" />;
  }
  if (status === 'error') {
    return <XCircle className="w-4 h-4 text-red-400 shrink-0" />;
  }
  if (status === 'aborted') {
    return <XCircle className="w-4 h-4 text-amber-400 shrink-0" />;
  }
  if (status === 'running') {
    return (
      <span className="inline-block w-2 h-2 rounded-full bg-cyan-400 animate-pulse shrink-0" />
    );
  }
  return <span className="inline-block w-2 h-2 rounded-full bg-slate-600 shrink-0" />;
}

const StepNav: React.FC = () => {
  const { stepStatuses, currentStep } = usePipelineStore();

  // Determine max navigable step: max done step index + 1
  const maxDone = Object.entries(stepStatuses)
    .filter(([, s]) => s === 'done')
    .reduce((acc, [k]) => Math.max(acc, parseInt(k)), 0);
  const maxNavigable = maxDone + 1;

  const handleStepClick = (stepIndex: number) => {
    // Allow all clicks (dev mode friendly)
    usePipelineStore.setState({ currentStep: stepIndex });
  };

  return (
    <nav className="flex flex-col gap-1 w-[200px] shrink-0 py-2">
      {STEPS.map(({ index, icon: Icon, label }) => {
        const status: StepStatus = stepStatuses[index] ?? 'pending';
        const isActive = index === currentStep;
        const isClickable = index <= maxNavigable;

        return (
          <button
            key={index}
            onClick={() => handleStepClick(index)}
            disabled={!isClickable}
            className={[
              'flex items-center gap-3 px-3 py-2 rounded-r-md text-sm transition-colors text-left',
              isActive
                ? 'border-l-2 bg-slate-800 text-cyan-300 font-semibold'
                : 'border-l-2 border-transparent text-slate-400 hover:text-slate-200 hover:bg-slate-800/50',
              isActive ? 'border-cyan-400' : '',
              !isClickable ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer',
            ].join(' ')}
            style={isActive ? { borderColor: '#00D4FF' } : undefined}
          >
            <Icon className="w-4 h-4 shrink-0" />
            <span className="flex-1 truncate">{label}</span>
            <StatusBadge status={status} />
          </button>
        );
      })}
    </nav>
  );
};

export default StepNav;
