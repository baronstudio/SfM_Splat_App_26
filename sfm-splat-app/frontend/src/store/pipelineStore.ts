import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import type {
  Project,
  WsMessage,
  LogEntry,
  StepStatus,
  TrainMetric,
  ExportFile,
  StepName,
  LogLevel,
  ProjectOperation,
  RunJob,
  JobLogEntry,
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

  // Training metrics — the `step N/M … splats S … psnr=P` bar line (§7.7)
  trainMetrics: TrainMetric[];

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

  // Actions — training metrics
  addTrainMetric: (metric: TrainMetric) => void;
  clearTrainMetrics: () => void;

  // Actions — export files
  setExportFiles: (files: ExportFile[]) => void;
  addExportFile: (file: ExportFile) => void;

  // Hydrate wizard state from a persisted project (on project selection / page reload)
  hydrateFromProject: (project: Project) => void;

  // Restore a run that was already going before this page existed (P7.1)
  hydrateRun: (job: RunJob, entries: JobLogEntry[]) => void;

  // WebSocket message dispatcher
  handleWsMessage: (msg: WsMessage) => void;
}

export const stepNameToIndex: Record<StepName, number> = {
  extract: 2,
  // Curation is step 2's second phase, so it reports against the same step.
  curate: 2,
  sfm: 3,
  // The `spirula sam` run attaches to step 3, and both the `spirula geometry`
  // run and the splat crop attach to step 4 — the same shape as `curate` being
  // step 2's second phase: each is separately re-runnable so a threshold change
  // never costs the expensive phase (CLAUDE.md §7.4, §7.5, §7.6b).
  masks: 3,
  geometry: 4,
  crop: 4,
  // The deliverable export attaches to step 4 too (§7.6c), and is the one pass
  // of the four whose output no later step reads.
  splat_export: 4,
  train: 4,
  // Step 5 meshes and then fills export/, its own delivery drawer (§7.10) —
  // two progress names, one wizard step, and the last of them.
  mesh: 5,
  export: 5,
};

// The training chart is fed by the trainer's own bar line, one every hundred
// steps — a 30 000-iteration run is 300 points, but a build that prints more
// often must not be able to hand recharts tens of thousands. Over the cap the
// series is halved, which keeps the whole range at half the resolution instead
// of dropping its head or its tail.
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

/** Fold one training bar line into the chart series.
 *
 * `spirula train` prints every number on one line every 100 steps (§7.7):
 *
 *   step 1101/3000 ( 36%) splats 58963 [elapsed 0:17 | ETA 0:26] rgb_loss=… ssim=… psnr=…
 *
 * so unlike LichtFeld Studio — which spread the iteration, the loss and PSNR
 * over two different lines, and made a chart that required all of them at once
 * stay empty for every run — a point here is normally complete. It is still
 * folded field by field rather than required whole: `ssim=0` and `psnr=20` both
 * occur, so a field is "missing" only when it is genuinely absent, never when
 * it is zero.
 */
function applyMetric(state: PipelineState, data: NonNullable<WsMessage['data']>): void {
  const num = (v: number | undefined) =>
    typeof v === 'number' && Number.isFinite(v) ? v : undefined;

  const loss = num(data.loss);
  const psnr = num(data.psnr);
  const ssim = num(data.ssim);
  const numGaussians = num(data.num_gaussians);
  if (loss === undefined && psnr === undefined
      && ssim === undefined && numGaussians === undefined) return;

  const last = state.trainMetrics[state.trainMetrics.length - 1];
  const iteration = num(data.iteration) ?? last?.iteration ?? 0;

  if (last && last.iteration === iteration) {
    if (loss !== undefined) last.loss = loss;
    if (psnr !== undefined) last.psnr = psnr;
    if (ssim !== undefined) last.ssim = ssim;
    if (numGaussians !== undefined) last.num_gaussians = numGaussians;
    return;
  }

  state.trainMetrics.push({ iteration, loss, psnr, ssim, num_gaussians: numGaussians });
  if (state.trainMetrics.length > MAX_METRIC_POINTS) {
    state.trainMetrics = state.trainMetrics.filter((_, i) => i % 2 === 0);
  }
}

export const usePipelineStore = create<PipelineState>()(
  immer((set) => ({
    // Initial state
    projects: [],
    currentProjectId: null,

    stepStatuses: { 1: 'pending', 2: 'pending', 3: 'pending', 4: 'pending', 5: 'pending' },
    currentStep: 1,
    pipelineRunning: false,

    wsConnected: false,
    logs: [],
    stepProgress: {},
    trainMetrics: [],
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
      set((state) => {
        // Changing project forgets everything that described the previous one.
        // `stepStatuses` is per-project state living in a store that outlives
        // every project, and only `hydrateFromProject` ever cleared it — which
        // the three *creation* paths do not call, because a brand-new row has
        // nothing to hydrate from. So a project created after a finished one
        // inherited its five green ticks, and worse, `useWebSocket` then wrote
        // that inherited dict into the new row the first time any step
        // reported: measured in `pipeline.db`, a project created 2026-08-30 and
        // extracted once was persisted `{"2":"done","3":"done","4":"done",
        // "5":"done"}` with `current_step: 2`.
        if (state.currentProjectId === id) return;
        state.currentProjectId = id;
        state.stepStatuses = { 1: 'pending', 2: 'pending', 3: 'pending', 4: 'pending', 5: 'pending' };
        state.stepProgress = {};
        state.trainMetrics = [];
        state.exportFiles = [];
        state.pipelineRunning = false;
        state.currentStep = 1;
      }),

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

    // Training metric actions
    addTrainMetric: (metric) =>
      set((state) => { state.trainMetrics.push(metric); }),

    clearTrainMetrics: () =>
      set((state) => { state.trainMetrics = []; }),

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
        state.stepStatuses = { 1: 'pending', 2: 'pending', 3: 'pending', 4: 'pending', 5: 'pending' };
        const saved = project.step_status as Record<string, string>;
        if (saved && typeof saved === 'object') {
          Object.entries(saved).forEach(([k, v]) => {
            const idx = parseInt(k, 10);
            if (idx >= 1 && idx <= 5 && ['pending', 'running', 'done', 'error', 'aborted'].includes(v)) {
              state.stepStatuses[idx] = v as StepStatus;
            }
          });
        }
        // Clamped to 5: the wizard lost its sixth step (Blender) on
        // 2026-08-30, and a project row saved at `current_step: 6` before that
        // would otherwise land on a step that has no component to render.
        state.currentStep = Math.min(Math.max(project.current_step, 1), 5);

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

    // Restore a run that started before this page did (TODO P7.1).
    //
    // Everything that described a running step lived here and nowhere else, so
    // a reload — or a browser opened on the LAN box halfway through — showed a
    // step disabled on `running` with an empty log, a bar at zero and **no
    // Abort button**, which renders on `pipelineRunning`. That is what made a
    // run that was working perfectly read as a run the page had killed.
    //
    // The job row is the source: `useRunRecovery` fetches it and the tail of
    // its log, and this folds both in. It sets the state the WS would have set
    // had the client been listening, and nothing else — no request is made and
    // no step is advanced.
    hydrateRun: (job, entries) =>
      set((state) => {
        if (job.project_id !== state.currentProjectId) return;

        state.pipelineRunning = true;
        state.stepStatuses[job.step] = 'running';
        state.currentStep = job.step;
        // Keyed by the run's own name, not by the step: four passes attach to a
        // step without being it, and `ProgressBar` reads the name.
        state.stepProgress[job.kind] = job.progress;

        entries.forEach((entry) => {
          state.logs.push({
            id: `restored-${job.id}-${entry.i}`,
            timestamp: entry.t,
            step: entry.step,
            level: (entry.level as LogLevel) ?? 'INFO',
            message: entry.message,
          });
          // The trainer puts its metrics on the same lines as its bar (§7.7),
          // and they were teed with the text — so the chart comes back too,
          // which for a 30 000-iteration run is the whole picture of the run.
          if (entry.data) applyMetric(state, entry.data);
        });
        if (state.logs.length > MAX_LOGS) {
          state.logs = state.logs.slice(state.logs.length - MAX_LOGS);
        }

        state.logs.push({
          id: `restored-${job.id}-head`,
          timestamp: new Date().toISOString(),
          step: job.kind,
          level: 'DEBUG',
          message:
            `[run] Rejoined a run already in progress: ${job.kind}`
            + ` (step ${job.step}), started ${job.started_at}Z,`
            + ` ${Math.round(job.progress * 100)} %,`
            + ` ${entries.length} of ${job.log_lines} log lines restored.`
            + (job.adopted
              ? ` This run outlived a backend restart and was re-attached`
                + `${job.adopted > 1 ? ` ${job.adopted} times` : ''}`
                + `${job.pid ? ` (tool pid ${job.pid})` : ''}.`
              : ''),
        });
      }),

    // WebSocket message dispatcher
    handleWsMessage: (msg) =>
      set((state) => {
        // Not our run: the bus is one channel for the whole app, and every
        // consumer maps a step name onto whatever project is open — so a run
        // belonging to another project used to move this one's bar (CLAUDE.md
        // §13.7, seen in P1.7 at 56 % with a live ETA). Only messages from a
        // run carry a project id; the project-operation bus carries none and
        // is deliberately global.
        if (
          msg.project_id
          && state.currentProjectId
          && msg.project_id !== state.currentProjectId
        ) {
          return;
        }

        // Progress is read before the switch, whatever the message was typed
        // as. A message legitimately carries a metric *and* a progress value —
        // `spirula train` puts both on every one of its bar lines (§7.7) — and
        // `websocket.py` picks the type by priority, testing `data` before
        // `progress`, so those arrive as `metric` and the bar never moved.
        // Fixing it here rather than reordering the priority: the type says
        // what the message is mainly about, and it should not decide whether a
        // number that is present gets used.
        if (msg.progress !== undefined) applyProgress(state, msg);

        // Same argument, one field further: a message's type does not decide
        // whether its *text* is worth showing either. `metric` was silently
        // dropping every line it carried, which for step 4 is the whole run —
        // the trainer says nothing else between loading the dataset and
        // finishing — and for step 3 it swallowed the one SUCCESS line naming
        // the registered count. Anything with a message is a log line.
        if (msg.message && (msg.type === 'log' || msg.type === 'metric')) {
          if (msg.step === 'project' && state.projectOp) {
            state.projectOp.message = msg.message;
          }
          state.logs.push({
            id: `log-${++logCounter}`,
            timestamp: msg.timestamp,
            step: msg.step,
            level: (msg.level as LogLevel) ?? 'INFO',
            message: msg.message,
          });
          if (state.logs.length > MAX_LOGS) {
            state.logs = state.logs.slice(state.logs.length - MAX_LOGS);
          }
        }

        switch (msg.type) {
          case 'log':
            // Already logged above, alongside the metric lines that carry text.
            break;
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
