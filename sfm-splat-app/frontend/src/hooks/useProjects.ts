import { useEffect } from 'react';
import apiClient from '@/api/client';
import { usePipelineStore } from '../store/pipelineStore';
import type { Project } from '../types';

export const useProjects = () => {
  const { setProjects, addProject, upsertProject, removeProject, setCurrentProject } =
    usePipelineStore();

  const fetchProjects = async () => {
    const response = await apiClient.get<Project[]>('/projects/');
    setProjects(response.data);
  };

  const createProject = async (name: string, settings?: object) => {
    const response = await apiClient.post<Project>('/projects/create', {
      name,
      ...(settings ? { settings } : {}),
    });
    addProject(response.data);
    return response.data;
  };

  const deleteProject = async (id: string) => {
    await apiClient.delete(`/projects/${id}`);
    removeProject(id);
  };

  /** Duplicate a project — files included — under a new name. */
  const copyProject = async (id: string, name: string) => {
    const response = await apiClient.post<Project>(`/projects/${id}/copy`, { name });
    addProject(response.data);
    return response.data;
  };

  /**
   * Wipe the artefacts of `steps` and rewind the wizard to the first of them.
   * `steps` omitted resets everything; the source video is kept either way.
   */
  const resetProject = async (id: string, steps?: number[]) => {
    const response = await apiClient.post<Project>(`/projects/${id}/reset`, {
      steps: steps ?? null,
    });
    upsertProject(response.data);
    return response.data;
  };

  /** Zip the project away. The row stays in the list, disabled. */
  const archiveProject = async (id: string) => {
    const response = await apiClient.post<Project>(`/projects/${id}/archive`);
    upsertProject(response.data);
    return response.data;
  };

  const unarchiveProject = async (id: string) => {
    const response = await apiClient.post<Project>(`/projects/${id}/unarchive`);
    upsertProject(response.data);
    return response.data;
  };

  const selectProject = (id: string) => {
    setCurrentProject(id);
  };

  useEffect(() => {
    fetchProjects();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    fetchProjects,
    createProject,
    deleteProject,
    copyProject,
    resetProject,
    archiveProject,
    unarchiveProject,
    selectProject,
  };
};

export default useProjects;
