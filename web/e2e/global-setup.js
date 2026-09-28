/**
 * Writes the signed-in storageState the projects point at.
 *
 * Kept in globalSetup rather than a per-test fixture for one reason: the
 * localStorage entry has to exist *before* the page's first script runs, or
 * AuthProvider resolves to "no session" and AppLayout redirects to /login
 * before any test code gets a word in.
 *
 * In live mode E2E_JWT must be set (scripts/e2e_seed.py prints one). We refuse
 * to run live without it rather than falling back to the placeholder token,
 * because the fallback would produce a suite that 401s on every request and
 * reports it as "the backend is broken".
 */

import { writeAuthState, E2E_USER_ID } from './fixtures/session.js';

export default async function globalSetup(config) {
  const baseURL = config.projects[0]?.use?.baseURL
    || process.env.E2E_BASE_URL
    || `http://127.0.0.1:${process.env.E2E_PORT || 5174}`;

  if (process.env.E2E_LIVE === '1' && !process.env.E2E_JWT) {
    throw new Error(
      'E2E_LIVE=1 but E2E_JWT is unset.\n'
      + 'Run:  .venv/bin/python scripts/e2e_seed.py seed\n'
      + 'and export the E2E_JWT it prints. See web/e2e/README.md.',
    );
  }

  const path = writeAuthState(baseURL);
  const mode = process.env.E2E_LIVE === '1' ? 'live (real JWT)' : 'mocked (placeholder token)';
  console.log(`[e2e] auth state -> ${path}  user=${E2E_USER_ID}  mode=${mode}`);
}
