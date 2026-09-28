import { defineConfig, devices } from '@playwright/test';

/**
 * Two projects, two very different jobs.
 *
 *   mocked  (default)  Vite dev server + a fake backend installed with
 *                      page.route. No secrets, no network, no database.
 *                      Runs anywhere, including CI. Proves things about the
 *                      frontend: which requests it makes, what it does with a
 *                      response, whether state survives a reload.
 *
 *   live    (opt-in)   The same browser against a real uvicorn running app.py
 *                      against the real Supabase, as a seeded synthetic user.
 *                      Proves things about the backend: the filter semantics
 *                      that a mock can only ever assume. Enabled with
 *                      E2E_LIVE=1; see e2e/README.md for the four things the
 *                      owner has to supply.
 *
 * PORT 5174, not 5173, so a run never fights the dev server the owner already
 * has open.
 */

const LIVE = process.env.E2E_LIVE === '1';
const PORT = Number(process.env.E2E_PORT || 5174);
const BASE_URL = process.env.E2E_BASE_URL || `http://127.0.0.1:${PORT}`;
const API_URL = process.env.E2E_API_URL || 'http://127.0.0.1:8000';

export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup.js',
  // Sequential by default: these tests share one Vite server and, in live
  // mode, one seeded row set. Parallelism here would buy seconds and cost
  // reproducibility.
  fullyParallel: false,
  workers: process.env.CI ? 1 : undefined,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : [['list']],
  timeout: 30_000,
  expect: { timeout: 7_000 },

  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    // Wide enough for JobTable's `hidden md:block` desktop table. Below
    // 768px the app renders the mobile card stack instead and every
    // table-scoped selector in the suite silently matches nothing.
    viewport: { width: 1440, height: 900 },
  },

  projects: [
    {
      name: 'mocked',
      testDir: './e2e/specs',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1440, height: 900 },
        storageState: './e2e/.auth/state.json',
      },
    },
    {
      name: 'live',
      testDir: './e2e/live',
      // Nothing in ./e2e/live is collected unless E2E_LIVE=1, so a plain
      // `npm run test:e2e` on a laptop with no .env cannot accidentally
      // point a browser at production data.
      testIgnore: LIVE ? [] : ['**/*'],
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1440, height: 900 },
        storageState: './e2e/.auth/state.json',
      },
    },
  ],

  webServer: [
    {
      command: `npm run dev:e2e -- --port ${PORT} --strictPort`,
      url: BASE_URL,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      stdout: 'pipe',
      stderr: 'pipe',
    },
    // Live mode also needs the real backend. `reuseExistingServer` means an
    // already-running uvicorn is used as-is rather than fought over.
    ...(LIVE ? [{
      command: 'bash ../scripts/e2e_backend.sh',
      url: `${API_URL}/api/health`,
      reuseExistingServer: true,
      timeout: 120_000,
      stdout: 'pipe',
      stderr: 'pipe',
    }] : []),
  ],
});
