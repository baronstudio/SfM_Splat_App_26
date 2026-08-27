import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import type {
  Project,
  WsMessage,
  LogEntry,
  StepStatus,
  LfsMetric,
  ExportFile,
  StepName,
  LogLevel,
  ProjectOperation,
} from '../types';

const MAX_LOGS = 500;

interface PipelineState {
  // Projects
  projects: Project[];
  currentProjectId: string | null;

  // Pipeline state
  stepStatuses: Record<number, StepStatus>;
  currentStep: number;
  pipelineRunning: boolean;

  // WebSocket connection status
  wsConnected: boolean;

  // Logs
  logs: LogEntry[];

  // Per-step progress
  stepProgress: Record<string, number>;

  // LFS metrics
  lfsMetrics: LfsMetric[];

  // Export files
  exportFiles: ExportFile[];

  // Project-level file operation in flight (copy / reset / archive / restore)
  projectOp: ProjectOperation | null;

  // Actions — projects
  setProjects: (projects: Project[]) => void;
  addProject: (project: Project) => void;
  upsertProject: (project: Project) => void;
  removeProject: (id: string) => void;
  setCurrentProject: (id: string | null) => void;

  // Actions — project operations
  startProjectOp: (op: { projectId: string; title: string; projectName: string }) => void;
  failProjectOp: (message: string) => void;
  endProjectOp: () => void;

  // Actions — websocket
  setWsConnected: (connected: boolean) => void;

  // Actions — logs
  addLog: (entry: LogEntry) => void;
  clearLogs: () => void;

  // Actions — pipeline
  setCurrentStep: (step: number) => void;
  setStepStatus: (step: number, status: StepStatus) => void;
  setStepProgress: (step: StepName, progress: number) => void;
  setPipelineRunning: (running: boolean) => void;
  confirmStep: (step: number) => void;

  // Actions — LFS metrics
  addLfsMetric: (metric: LfsMetric) => void;
  clearLfsMetrics: () => void;

  // Actions — export files
  setExportFiles: (files: ExportFile[]) => void;
  addExportFile: (file: ExportFile) => void;

  // Hydrate wizard state from a persisted project (on project selection / page reload)
  hydrateFromProject: (project: Project) => void;

  // WebSocket message dispatcher
  handleWsMessage: (msg: WsMessage) => void;
}

const stepNameToIndex: Record<StepName, number> = {
  extract: 2,
  // Curation is step 2's second phase, so it reports against the same step.
  curate: 2,
  rc: 3,
  // The mask run is step 3's second RealityScan process, the same shape as
  // `curate` being step 2's second phase (TODO P4).
  masks: 3,
  lfs: 4,
  export: 5,
  blender: 6,
};

// The training chart is fed by whatever LichtFeld Studio prints, which is a bar
// redrawn every hundred iterations — a 30 000-iteration run is 300 points, but
// a build that redraws more often must not be able to hand recharts tens of
// thousands. Over the cap the series is halved, which keeps the whole range at
// half the resolution instead of dropping its head or its tail.
const MAX_METRIC_POINTS = 800;

let logCounter = 0;

/** Move a step's bar, and close the step out when the runner reports 1.0. */
function applyProgress(state: PipelineState, msg: WsMessage): void {
  const progress = msg.progress;
  if (progress === undefined) return;

  if (msg.step === 'project') {
    // Not a wizard step: the file operations report here, and only the modal
    // cares.
    if (state.projectOp) {
      state.projectOp.progress = progress;
      if (msg.message) state.projectOp.message = msg.message;
    }
    return;
  }

  state.stepProgress[msg.step] = progress;

  // progress = 1.0 means the step runner finished — a fallback for messages
  // that carry progress but no explicit status field.
  if (progress < 1.0) return;
  const stepIdx = stepNameToIndex[msg.step as StepName];
  if (stepIdx === undefined || state.stepStatuses[stepIdx] !== 'running') return;
  const level = msg.level ?? 'SUCCESS';
  if (level !== 'SUCCESS' && level !== 'INFO') return;

  state.stepStatuses[stepIdx] = 'done';
  state.pipelineRunning = false;
  console.debug(
    `[WIZARD-DEBUG] progress=1.0 on step ${msg.step}(${stepIdx})`
    + ' → stepStatuses set to done, pipelineRunning=false',
  );
}

/** Fold one parsed LichtFeld Studio line into the training series.
 *
 * Partial by design. v0.5.3 spreads the numbers over two different lines —
 * `Training […] 100/300 | Loss: 0.1391 | Splats: 281029` carries the iteration,
 * the loss and the gaussian count, while PSNR appears only on the
 * `[Evaluation at step N] PSNR: …` line an `--eval` run prints. Requiring all
 * four at once, as this did, meant no point ever qualified and the chart stayed
 * empty for every run. So a message contributes what it has: same iteration as
 * the last point merges into it, a new one appends, and a field nobody reported
 * stays undefined rather than being invented — recharts draws no line for a
 * series that is never present, which is the honest picture of a run with no
 * evaluation.
 */
function applyMetric(state: PipelineState, data: NonNullable<WsMessage['data']>): void {
  const num = (v: number | undefined) =>
    typeof v === 'number' && Number.isFinite(v) ? v : undefined;

  const loss = num(data.loss);
  const psnr = num(data.psnr);
  const numGaussians = num(data.num_gaussians);
  if (loss === undefined && psnr === undefined && numGaussians === undefined) return;

  const last = state.lfsMetrics[state.lfsMetrics.length - 1];
  const iteration = num(data.iteration) ?? last?.iteration ?? 0;

  if (last && last.iteration === iteration) {
    if (loss !== undefined) last.loss = loss;
    if (psnr !== undefined) last.psnr = psnr;
    if (numGaussians !== undefined) last.num_gaussians = numGaussians;
    return;
  }

  state.lfsMetrics.push({ iteration, loss, psnr, num_gaussians: numGaussians });
  if (state.lfsMetrics.length > MAX_METRIC_POINTS) {
    state.lfsMetrics = state.lfsMetrics.filter((_, i) => i % 2 === 0);
  }
}

export const usePipelineStore = create<PipelineState>()(
  immer((set) => ({
    // Initial state
    projects: [],
    currentProjectId: null,

    stepStatuses: { 1: 'pending', 2: 'pending', 3: 'pending', 4: 'pending', 5: 'pending', 6: 'pending' },
    currentStep: 1,
    pipelineRunning: false,

    wsConnected: false,
    logs: [],
    stepProgress: {},
    lfsMetrics: [],
    exportFiles: [],
    projectOp: null,

    // Project actions
    setProjects: (projects) =>
      set((state) => { state.projects = projects; }),

    addProject: (project) =>
      set((state) => { state.projects.push(project); }),

    // Copy / reset / archive all hand back the whole project row, so the list
    // takes it as-is rather than re-fetching everything for one changed tile.
    upsertProject: (project) =>
      set((state) => {
        const index = state.projects.findIndex((p) => p.id === project.id);
        if (index === -1) state.projects.push(project);
        else state.projects[index] = project;
      }),

    removeProject: (id) =>
      set((state) => {
        state.projects = state.projects.filter((p) => p.id !== id);
        if (state.currentProjectId === id) state.currentProjectId = null;
      }),

    setCurrentProject: (id) =>
      set((state) => { state.currentProjectId = id; }),

    // Project operation actions. The modal is opened by the request that starts
    // the work and closed by the one that finishes it; what happens in between
    // arrives on the WS bus under the step name 'project'.
    startProjectOp: ({ projectId, title, projectName }) =>
      set((state) => {
        state.projectOp = {
          projectId, title, projectName,
          progress: 0,
          message: 'Starting…',
          error: null,
        };
      }),

    failProjectOp: (message) =>
      set((state) => {
        if (state.projectOp) state.projectOp.error = message;
      }),

    endProjectOp: () =>
      set((state) => { state.projectOp = null; }),

    // WebSocket connection status
    setWsConnected: (connected) =>
      set((state) => { state.wsConnected = connected; }),

    // Log actions
    addLog: (entry) =>
      set((state) => {
        state.logs.push(entry);
        if (state.logs.length > MAX_LOGS) {
          state.logs = state.logs.slice(state.logs.length - MAX_LOGS);
        }
      }),

    clearLogs: () =>
      set((state) => { state.logs = []; }),

    // Pipeline actions
    setCurrentStep: (step) =>
      set((state) => { state.currentStep = step; }),

    setStepStatus: (step, status) =>
      set((state) => { state.stepStatuses[step] = status; }),

    setStepProgress: (step, progress) =>
      set((state) => { state.stepProgress[step] = progress; }),

    setPipelineRunning: (running) =>
      set((state) => { state.pipelineRunning = running; }),

    confirmStep: (step) =>
      set((state) => { state.stepStatuses[step] = 'done'; }),

    // LFS metrics actions
    addLfsMetric: (metric) =>
      set((state) => { state.lfsMetrics.push(metric); }),

    clearLfsMetrics: () =>
      set((state) => { state.lfsMetrics = []; }),

    // Export file actions
    setExportFiles: (files) =>
      set((state) => { state.exportFiles = files; }),

    addExportFile: (file) =>
      set((state) => { state.exportFiles.push(file); }),

    // Restore wizard state from a saved project
    hydrateFromProject: (project) =>
      set((state) => {
        const prevStatuses = { ...state.stepStatuses };
        const prevCurrentStep = state.currentStep;

        // Reset all steps to pending, then apply persisted statuses
        state.stepStatuses = { 1: 'pending', 2: 'pending', 3: 'pending', 4: 'pending', 5: 'pending', 6: 'pending' };
        const saved = project.step_status as Record<string, string>;
        if (saved && typeof saved === 'object') {
          Object.entries(saved).forEach(([k, v]) => {
            const idx = parseInt(k, 10);
            if (idx >= 1 && idx <= 6 && ['pending', 'running', 'done', 'error', 'aborted'].includes(v)) {
              state.stepStatuses[idx] = v as StepStatus;
            }
          });
        }
        state.currentStep = Math.max(project.current_step, 1);

        // ── Debug log ──────────────────────────────────────────────────────
        console.debug(
          '[WIZARD-DEBUG] hydrateFromProject',
          `project='${project.name}'`,
          `DB_current_step=${project.current_step} → UI_currentStep=${state.currentStep}`,
          `prev_currentStep=${prevCurrentStep}`,
          `DB_step_status=`, saved,
          `prev_stepStatuses=`, prevStatuses,
          `new_stepStatuses=`, { ...state.stepStatuses },
        );
        state.logs.push({
          id: `hydrate-${++logCounter}`,
          timestamp: new Date().toISOString(),
          step: 'pipeline',
          level: 'DEBUG',
          message:
            `[WIZARD-DEBUG] hydrateFromProject: project='${project.name}'`
            + ` DB_current_step=${project.current_step}→UI=${state.currentStep}`
            + ` stepStatuses=${JSON.stringify(state.stepStatuses)}`
            + ` ⚠️ Watch Step5 useEffect: will auto-start if step4=done & step5=pending & currentStep=5`,
        });
      }),

    // WebSocket message dispatcher
    handleWsMessage: (msg) =>
      set((state) => {
        // Progress is read before the switch, whatever the message was typed
        // as. A message legitimately carries a metric *and* a progress value —
        // LichtFeld Studio sends both on every training-bar line — and
        // `websocket.py` picks the type by priority, testing `data` before
        // `progress`, so those arrive as `metric` and the bar never moved.
        // Fixing it here rather than reordering the priority: the type says
        // what the message is mainly about, and it should not decide whether a
        // number that is present gets used.
        if (msg.progress !== undefined) applyProgress(state, msg);

        switch (msg.type) {
          case 'log': {
            if (msg.step === 'project' && state.projectOp && msg.message) {
              state.projectOp.message = msg.message;
            }
            const entry: LogEntry = {
              id: `log-${++logCounter}`,
              timestamp: msg.timestamp,
              step: msg.step,
              level: (msg.level as LogLevel) ?? 'INFO',
              message: msg.message ?? '',
            };
            state.logs.push(entry);
            if (state.logs.length > MAX_LOGS) {
              state.logs = state.logs.slice(state.logs.length - MAX_LOGS);
            }
            break;
          }
          case 'progress':
            // Already applied above, for every message type at once.
            break;
          case 'metric': {
            if (msg.data) applyMetric(state, msg.data);
            break;
          }
          case 'status': {
            const stepIdx = stepNameToIndex[msg.step as StepName];
            if (stepIdx !== undefined && msg.level) {
              const statusMap: Record<string, StepStatus> = {
                INFO: 'running',
                SUCCESS: 'done',
                ERROR: 'error',
                WARNING: 'running',
              };
              // msg.status is the explicit state; the level map is only a
              // fallback for older messages that carry no status field. An abort
              // arrives as level=WARNING, which the map reads as 'running' — so
              // without this the aborted step would spin forever.
              const explicit: StepStatus[] = ['running', 'done', 'error', 'aborted'];
              const newStatus = explicit.includes(msg.status as StepStatus)
                ? (msg.status as StepStatus)
                : statusMap[msg.level];
              if (newStatus) {
                const prevStatus = state.stepStatuses[stepIdx];
                state.stepStatuses[stepIdx] = newStatus;
                console.debug(
                  `[WIZARD-DEBUG] WS status: step=${msg.step}(${stepIdx})`
                  + ` ${prevStatus} → ${newStatus}  pipelineRunning=${state.pipelineRunning}`,
                );
                state.logs.push({
                  id: `status-${++logCounter}`,
                  timestamp: msg.timestamp ?? new Date().toISOString(),
                  step: msg.step,
                  level: 'DEBUG',
                  message:
                    `[WIZARD-DEBUG] stepStatus: step=${msg.step}(${stepIdx})`
                    + ` ${prevStatus}→${newStatus}`
                    + (newStatus === 'done'
                      ? ' ✅ Waiting for user click to advance wizard'
                      : ''),
                });
                if (newStatus === 'running') {
                  state.currentStep = stepIdx;
                  state.pipelineRunning = true;
                }
                if (newStatus === 'done' || newStatus === 'error' || newStatus === 'aborted') {
                  // Single-step pipeline: mark as not running as soon as the step finishes
                  state.pipelineRunning = false;
                }
              }
            }
            break;
          }
          case 'file_ready': {
            if (msg.file) {
              state.exportFiles.push({
                filename: msg.file,
                url: `/api/files/${msg.file}`,
                size_bytes: 0,
              });
            }
            break;
          }
        }
      }),
  }))
);
