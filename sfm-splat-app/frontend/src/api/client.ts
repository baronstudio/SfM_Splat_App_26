import axios from 'axios';

/**
 * Same-origin by default — CLAUDE.md §1's "no hardcoded `localhost` in the
 * frontend API client".
 *
 * The page is served by Vite (dev) or by whatever serves the built bundle, and
 * both route `/api`, `/static` and `/ws` to the backend, so the browser always
 * reaches it through the host the user actually typed. With an absolute
 * `http://localhost:8000` here, a machine on the LAN opening the staging server
 * asks *its own* localhost for the API and every call fails.
 *
 * `VITE_API_BASE` is the escape hatch for the day the two are genuinely split
 * across hosts.
 */
const API_BASE = import.meta.env.VITE_API_BASE ?? '/api';

const client = axios.create({
  baseURL: API_BASE,
});

/**
 * Absolute URL of a `/static/...` path, resolved against the API host.
 *
 * With the default same-origin base this returns the path unchanged, which is
 * what the browser wants; it only prefixes a host when `VITE_API_BASE` names
 * one.
 */
export function staticUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  const base = (client.defaults.baseURL ?? '').replace(/\/api\/?$/, '');
  return `${base}${path.startsWith('/') ? '' : '/'}${path}`;
}

export default client;
