import client from '../api/client';
import { usePipelineStore } from '../store/pipelineStore';

export const usePipeline = () => {
  const { setPipelineRunning } = usePipelineStore();

  const startPipeline = async (
    projectId: string,
    fromStep: number,
    settings: object
  ) => {
    const response = await client.post('/pipeline/start', {
      project_id: projectId,
      start_from_step: fromStep,
      settings,
    });
    setPipelineRunning(true);
    return response.data;
  };

  const controlPipeline = async (
    projectId: string,
    action: 'pause' | 'resume' | 'abort'
  ) => {
    const response = await client.post('/pipeline/control', {
      project_id: projectId,
      action,
    });
    if (action === 'abort') setPipelineRunning(false);
    return response.data;
  };

  /** Generate RealityScan's own masks over the saved alignment (TODO P4).
   *
   *  Not `/start`: it must not re-align. Same body and same guards as
   *  `/analyze`, and the same `_running_tasks` slot, so the abort button and
   *  the one-job-at-a-time rule cover it.
   */
  const generateMasks = async (projectId: string, settings: object) => {
    const response = await client.post('/pipeline/masks', {
      project_id: projectId,
      settings,
    });
    setPipelineRunning(true);
    return response.data;
  };

  const fetchStatus = async (projectId: string) => {
    const response = await client.get('/pipeline/status', {
      params: { project_id: projectId },
    });
    return response.data;
  };

  return { startPipeline, controlPipeline, generateMasks, fetchStatus };
};

export default usePipeline;
