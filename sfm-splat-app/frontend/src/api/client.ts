import axios from 'axios';

const client = axios.create({
  baseURL: 'http://localhost:8000/api',
});

/**
 * Absolute URL of a `/static/...` path, resolved against the API host.
 *
 * The backend hands out root-relative static paths, which the dev server on
 * :5173 would resolve against itself. Deriving the host from `baseURL` keeps
 * the one hardcoded `localhost` in this file (CLAUDE.md §1) instead of
 * spreading it to every component that loads a project file.
 */
export function staticUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  const base = (client.defaults.baseURL ?? '').replace(/\/api\/?$/, '');
  return `${base}${path.startsWith('/') ? '' : '/'}${path}`;
}

export default client;
