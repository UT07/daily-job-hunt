/**
 * The auth gate.
 *
 * Nothing here types a password. The owner's credentials are not available to
 * this suite by design, so the two ends are covered instead: no session at all
 * must land on /login, and a session that the backend rejects mid-flight must
 * put the user back there rather than leaving a half-rendered dashboard.
 */

import { test, expect } from '../fixtures/test.js';

test.describe('unauthenticated', () => {
  // Explicitly blank: this file's whole point is having no session.
  test.use({ storageState: { cookies: [], origins: [] } });

  test('the dashboard is not reachable without a session', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Job Dashboard' })).toHaveCount(0);
  });

  test('a deep link to a job is not reachable either', async ({ page }) => {
    await page.goto('/jobs/job-s-tier');
    await expect(page).toHaveURL(/\/login$/);
  });

  test('Add Job is not reachable either', async ({ page }) => {
    await page.goto('/add-job');
    await expect(page).toHaveURL(/\/login$/);
  });

  test('an unknown route still lands on login, not a blank page', async ({ page }) => {
    await page.goto('/analytics');
    await expect(page).toHaveURL(/\/login$/);
  });

  test('the login page offers email, password and Google', async ({ page }) => {
    await page.goto('/login');
    await expect(page.getByLabel(/email/i)).toBeVisible();
    await expect(page.getByLabel(/password/i)).toBeVisible();
    await expect(page.getByRole('button', { name: /Continue with Google/ })).toBeVisible();
  });
});

test.describe('session expiry', () => {
  test('a 401 from the API bounces the user back to login', async ({ page, dashboard, api }) => {
    await dashboard.goto();
    await expect(dashboard.heading).toBeVisible();

    // The refresh token died while the user was away: every subsequent call
    // 401s. api.js is supposed to clear the local session on 401, which makes
    // AuthProvider drop the user and AppLayout redirect. Before that helper
    // existed this rendered a silently broken dashboard instead.
    api.override('/api/dashboard/jobs', { status: 401, body: { detail: 'Invalid or expired token' } });
    api.override('/api/profile', { status: 401, body: { detail: 'Invalid or expired token' } });

    await dashboard.applyFilters.click();
    await expect(page).toHaveURL(/\/login$/, { timeout: 15_000 });
  });

  /**
   * KNOWN DEFECT -- marked `test.fail` so the suite stays green and turns red the
   * day it is fixed (at which point delete the marker).
   *
   * api.js's 401 handler is documented as turning "silently broken" into
   * "bounced to login". It only does that while Supabase itself is reachable:
   * supabase-js's signOut POSTs to /auth/v1/logout even for scope:'local', and
   * on a network-level failure it returns the error WITHOUT removing the
   * stored session (GoTrueClient._signOut). So an expired session plus an
   * unreachable auth host leaves the user staring at a dashboard that 401s on
   * every request, with no redirect and no explanation.
   *
   * Repro: expire the session, go offline (or block supabase.co), reload.
   * Expected: /login.  Observed: the dashboard, with an error banner.
   * Fix: treat a failed local sign-out as a sign-out anyway -- clear
   * localStorage directly when signOut rejects.
   */
  test.fail('a 401 bounces to login even when Supabase itself is unreachable', async ({ page, dashboard, api }) => {
    await dashboard.goto();
    api.supabaseAuthReachable = false;
    api.override('/api/dashboard/jobs', { status: 401, body: { detail: 'Invalid or expired token' } });
    api.override('/api/profile', { status: 401, body: { detail: 'Invalid or expired token' } });

    await dashboard.applyFilters.click();
    await expect(page).toHaveURL(/\/login$/, { timeout: 4_000 });
  });
});
