/**
 * The extended `test` used by every mocked spec.
 *
 * Fixtures:
 *   jobs         option — the corpus the fake backend serves. `test.use({ jobs: ... })`.
 *   profile      option — overrides merged into GET /api/profile.
 *   api          the MockApi instance: request log in, controlled responses out.
 *   dashboard    page object for the dashboard.
 *   consoleErrors  every console error / page error seen during the test.
 */

import { test as base, expect } from '@playwright/test';
import { MockApi } from './mockApi.js';
import { standardJobs } from './jobs.js';
import { Dashboard } from './dashboard.js';

export const test = base.extend({
  /**
   * The fake backend, always installed.
   *
   * `auto: true` on purpose: a spec that forgets to list `api` would otherwise
   * get a page whose every fetch falls through the Vite proxy to a backend
   * that is not running, which surfaces as an inscrutable 30s timeout rather
   * than "you forgot a fixture".
   *
   * The corpus is NOT a `test.use()` option, deliberately. Playwright unwraps
   * an array passed through `use()` as its own `[value, options]` fixture
   * tuple, and treats a function as a fixture body -- so neither shape
   * survives. A spec that wants a different corpus sets it before navigating:
   *
   *     api.setJobs([makeJob({ ... })]);
   *     await dashboard.goto();
   */
  api: [async ({ page }, use) => {
    const api = new MockApi({ jobs: standardJobs() });
    await api.install(page);
    await use(api);
  }, { auto: true }],

  dashboard: async ({ page }, use) => {
    await use(new Dashboard(page));
  },

  // Surfacing console errors is not decoration: a React render that throws
  // inside an error boundary, or an unhandled promise rejection in a fetch
  // path, can leave a page that *looks* fine. Specs opt in by asserting on
  // this array; it is always collected.
  consoleErrors: async ({ page }, use) => {
    const errors = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push(msg.text());
    });
    page.on('pageerror', (err) => errors.push(`pageerror: ${err.message}`));
    await use(errors);
  },
});

export { expect };

/** Query params that the app is expected to send on a default dashboard load.
 *  Imported by the contract spec so the expectation lives in exactly one place. */
export const DEFAULT_LIST_PARAMS = {
  page: '1',
  per_page: '25',
  min_score: '60',
  hide_expired: 'true',
  sort_by: 'first_seen',
  sort_order: 'desc',
};
