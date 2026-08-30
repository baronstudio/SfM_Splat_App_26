import React from 'react';
import { BookOpen } from 'lucide-react';

const STEP_HELP_PAGES: Record<number, string> = {
  0: '/help/index.html',
  1: '/help/step1.html',
  2: '/help/step2.html',
  3: '/help/step3.html',
  4: '/help/step4.html',
  5: '/help/step5.html',
};

interface HelpPanelProps {
  currentStep: number;
  className?: string;
}

export const HelpPanel: React.FC<HelpPanelProps> = ({ currentStep, className }) => {
  const src = STEP_HELP_PAGES[currentStep] ?? '/help/index.html';

  return (
    <div className={`flex flex-col h-full ${className ?? ''}`}>
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-slate-900 border-b border-slate-800 shrink-0">
        <BookOpen className="w-3.5 h-3.5 text-slate-400" />
        <span className="text-xs font-semibold text-slate-400 uppercase tracking-widest">Help</span>
        {currentStep > 0 && (
          <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs bg-slate-700 text-slate-300">
            Step {currentStep}
          </span>
        )}
      </div>

      {/* Iframe */}
      <iframe
        key={src}
        src={src}
        title="Help"
        className="flex-1 w-full border-none bg-slate-950"
        sandbox="allow-same-origin allow-popups"
      />
    </div>
  );
};

export default HelpPanel;
