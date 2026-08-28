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

  /** Write `masks/` with `spirula sam` (CLAUDE.md §7.4).
   *
   *  Not `/start`: it must not re-align. The masks are an *input* to step 3 —
   *  `sfm auto` adopts a `masks/` sibling of the image directory with no flag —
   *  so this never marks step 3 done. Same body and same guards as `/analyze`,
   *  and the same `_running_tasks` slot, so the abort button and the
   *  one-job-at-a-time rule cover it.
   */
  const generateMasks = async (projectId: string, settings: object) => {
    const response = await client.post('/pipeline/masks', {
      project_id: projectId,
      settings,
    });
    setPipelineRunning(true);
    return response.data;
  };

  /** Write `sfm/normals/` and `sfm/depths/` with `spirula geometry` (§7.5).
   *
   *  Not `/start`: it must not re-train. The maps land *inside* the dataset
   *  step 4 reads, so the pairing costs no flag — and this never marks step 4
   *  done. Same guards and the same `_running_tasks` slot as the two above.
   */
  const runGeometry = async (projectId: string, settings: object) => {
    const response = await client.post('/pipeline/geometry', {
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

  return { startPipeline, controlPipeline, generateMasks, runGeometry, fetchStatus };
};

export default usePipeline;
