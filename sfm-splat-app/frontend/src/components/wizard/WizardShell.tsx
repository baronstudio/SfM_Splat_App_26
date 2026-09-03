import React, { lazy, Suspense, useState } from 'react';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import AppTitle from '@/components/AppTitle';
import { AlertTriangle, BookOpen, ChevronRight, FolderOpen, Home, PanelBottomClose, PanelBottomOpen, PanelRightClose, PanelRightOpen, Plus, Settings } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import StepNav from './StepNav';
import LiveLog from '@/components/panels/LiveLog';
import HardwareGauges from '@/components/panels/HardwareGauges';
import HelpPanel from '@/components/panels/HelpPanel';
import AppSetupPanel from '@/components/settings/AppSetupPanel';
import ProjectOperationDialog from '@/components/projects/ProjectOperationDialog';
import ProjectInfoPanel from '@/components/projects/ProjectInfoPanel';
import { usePipelineStore } from '@/store/pipelineStore';
import { usePipeline } from '@/hooks/usePipeline';
import { useProjects } from '@/hooks/useProjects';
import { useRunRecovery } from '@/hooks/useRunRecovery';

const Step1 = lazy(() => import('./steps/Step1_Import'));
const Step2 = lazy(() => import('./steps/Step2_Extract'));
const Step3 = lazy(() => import('./steps/Step3_Sfm'));
const Step4 = lazy(() => import('./steps/Step4_Train'));
const Step5 = lazy(() => import('./steps/Step5_Mesh'));

const STEP_COMPONENTS: Record<number, React.LazyExoticComponent<React.FC>> = {
  1: Step1,
  2: Step2,
  3: Step3,
  4: Step4,
  5: Step5,
};

const WizardShell: React.FC<{ onBackToHome?: () => void }> = ({ onBackToHome }) => {
  const { currentProjectId, currentStep, projects, pipelineRunning, setCurrentProject, setCurrentStep, hydrateFromProject } = usePipelineStore();
  const { controlPipeline } = usePipeline();
  const { createProject, selectProject } = useProjects();
  // A run started before this page existed is put back on screen here rather
  // than in a step component: it must be found whichever step is open, and the
  // Abort button lives in this shell (TODO P7.1).
  useRunRecovery();
  const [logVisible, setLogVisible] = useState(true);
  const [helpVisible, setHelpVisible] = useState(true);
  const [setupOpen, setSetupOpen] = useState(false);

  const currentProject = projects.find((p) => p.id === currentProjectId);
  // Archived projects are not selectable: their files are zipped away.
  const liveProjects = projects.filter((p) => !p.archived);
  const StepComponent = STEP_COMPONENTS[currentStep];

  const handleAbort = async () => {
    if (!currentProjectId) return;
    const confirmed = window.confirm('Abort the current pipeline run? This cannot be undone.');
    if (!confirmed) return;
    await controlPipeline(currentProjectId, 'abort');
  };

  const handleNewProject = async () => {
    const name = prompt('Project name:');
    if (!name?.trim()) return;
    setCurrentProject(null);
    setCurrentStep(1);
    await createProject(name.trim());
  };

  const handleSelectProject = (id: string) => {
    const project = projects.find((p) => p.id === id);
    selectProject(id);
    if (project) {
      hydrateFromProject(project);
    } else {
      setCurrentStep(1);
    }
  };

  return (
    <div className="flex flex-col h-screen bg-slate-900 text-slate-100">
      {/* TOP BAR */}
      <header className="relative flex items-center justify-between px-4 py-2 bg-slate-800 border-b border-slate-700 shrink-0">
        <div className="flex items-center gap-2 text-sm font-medium">
          {/* Back to home */}
          {(onBackToHome || true) && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setCurrentProject(null);
                setCurrentStep(1);
              }}
              title="Back to Home"
              className="text-slate-400 hover:text-slate-100 px-2 h-7"
            >
              <Home className="w-4 h-4" />
            </Button>
          )}
          <Separator orientation="vertical" className="h-5 bg-slate-600" />
          {/* Projects dropdown */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm" className="gap-1 text-slate-300 hover:text-slate-100 px-2 h-7">
                <FolderOpen className="w-4 h-4" />
                <span className="max-w-[160px] truncate">
                  {currentProject ? currentProject.name : 'No project'}
                </span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" side="bottom" className="w-56 bg-slate-800 border-slate-700 text-slate-100">
              {liveProjects.map((p) => (
                <DropdownMenuItem
                  key={p.id}
                  className="cursor-pointer hover:bg-slate-700"
                  onClick={() => handleSelectProject(p.id)}
                >
                  <span className="truncate">{p.name}</span>
                </DropdownMenuItem>
              ))}
              {liveProjects.length > 0 && <DropdownMenuSeparator className="bg-slate-700" />}
              <DropdownMenuItem className="cursor-pointer hover:bg-slate-700 gap-2" onClick={handleNewProject}>
                <Plus className="w-3 h-3" />
                New Project
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <ChevronRight className="w-4 h-4 text-slate-500" />
          <span style={{ color: '#00D4FF' }}>Step {currentStep}/5</span>
        </div>

        {/* App name + version, centred over the bar */}
        <AppTitle />
        <div className="flex items-center gap-2">
          {/* Application setup (defaults for every wizard step) */}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setSetupOpen(true)}
            title="Application setup"
            className="text-slate-400 hover:text-slate-100"
          >
            <Settings className="w-4 h-4" />
          </Button>
          <Separator orientation="vertical" className="h-5 bg-slate-600" />
          {/* Toggle Help panel */}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setHelpVisible((v) => !v)}
            title={helpVisible ? 'Hide Help' : 'Show Help'}
            className="text-slate-400 hover:text-slate-100"
          >
            {helpVisible ? (
              <PanelRightClose className="w-4 h-4" />
            ) : (
              <PanelRightOpen className="w-4 h-4" />
            )}
            <BookOpen className="w-3 h-3 ml-0.5" />
          </Button>
          {/* Toggle Live Log panel */}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setLogVisible((v) => !v)}
            title={logVisible ? 'Hide Log' : 'Show Log'}
            className="text-slate-400 hover:text-slate-100"
          >
            {logVisible ? (
              <PanelBottomClose className="w-4 h-4" />
            ) : (
              <PanelBottomOpen className="w-4 h-4" />
            )}
          </Button>
          <Separator orientation="vertical" className="h-5 bg-slate-600" />
          <Button
            variant="destructive"
            size="sm"
            disabled={!pipelineRunning || !currentProjectId}
            onClick={handleAbort}
            className="gap-1"
          >
            <AlertTriangle className="w-4 h-4" />
            Abort
          </Button>
        </div>
      </header>

      {/* BODY */}
      <div className="flex flex-col flex-1 overflow-hidden">

        {/* MAIN ROW: left nav + center content + right help */}
        <div className="flex flex-1 overflow-hidden">
          {/* LEFT: Step navigator */}
          {/* The navigator scrolls; the gauges are pinned under it. They
              describe the machine rather than the project, so they stay put
              while the step content changes — which is the point, since what
              they are for is watching a run that has taken the user somewhere
              else in the wizard. */}
          <aside className="w-[200px] shrink-0 border-r border-slate-700 flex flex-col overflow-hidden">
            <div className="flex-1 overflow-y-auto overflow-x-hidden">
              <StepNav />
              <ProjectInfoPanel />
            </div>
            <HardwareGauges />
          </aside>

          {/* CENTER: Current step content */}
          <main className="flex-1 overflow-y-auto p-4">
            <ErrorBoundary label={`Step ${currentStep}`}>
              <Suspense
                fallback={
                  <div className="flex items-center justify-center h-full text-slate-400">
                    Loading step...
                  </div>
                }
              >
                {StepComponent ? (
                  <StepComponent />
                ) : (
                  <div className="text-slate-400">Unknown step</div>
                )}
              </Suspense>
            </ErrorBoundary>
          </main>

          {/* RIGHT: Help panel (collapsible) */}
          {helpVisible && (
            <aside className="w-[300px] shrink-0 border-l border-slate-700 overflow-hidden flex flex-col">
              <HelpPanel currentStep={currentStep} />
            </aside>
          )}
        </div>

        {/* BOTTOM: Live log (collapsible) */}
        {logVisible && (
          <div className="h-[200px] shrink-0 border-t border-slate-700 overflow-hidden flex flex-col">
            <LiveLog />
          </div>
        )}
      </div>

      <AppSetupPanel open={setupOpen} onClose={() => setSetupOpen(false)} />

      {/* Blocking progress for copy / reset / archive / restore. Mounted here,
          not in the project list, so changing step does not hide it (§14). */}
      <ProjectOperationDialog />
    </div>
  );
};

export default WizardShell;
