/**
 * The "Past / Outdated" shelf (feat/dashboard-archive-ui, c0ce222).
 *
 * The 14-to-30-day band. Its design constraints are all about NOT being
 * clutter: collapsed by default, and renders literally nothing when empty --
 * a permanently visible "Past / Outdated (0)" header is exactly what the
 * feature exists to remove.
 */

import { test, expect } from '../fixtures/test.js';
import { makeJob, daysAgo } from '../fixtures/jobs.js';

function staleJobs(n = 3) {
  return Array.from({ length: n }, (_, i) => makeJob({
    job_id: `stale-${i}`,
    title: `Stale Role ${i}`,
    company: `Stale Co ${i}`,
    match_score: 80,
    first_seen: daysAgo(16 + i),
  }));
}

test.describe('when the band is empty', () => {
  test('nothing is rendered at all — no header, no zero count', async ({ dashboard, api }) => {
    api.setJobs([makeJob({ job_id: 'fresh', title: 'Fresh Role', company: 'Fresh Co', match_score: 90, first_seen: daysAgo(1) })]);
    await dashboard.goto();
    await expect(dashboard.rows).toHaveCount(1);
    await expect(dashboard.pastShelf).toHaveCount(0);
    await expect(dashboard.page.getByText('Past / Outdated')).toHaveCount(0);
  });
});

test.describe('when the band has jobs', () => {
  test('the shelf appears collapsed, with a count and the age rule spelled out', async ({ dashboard, api }) => {
    api.setJobs([...staleJobs(3), makeJob({ job_id: 'fresh', title: 'Fresh Role', company: 'Fresh Co', match_score: 90, first_seen: daysAgo(1) })]);
    await dashboard.goto();

    await expect(dashboard.pastShelf).toBeVisible();
    await expect(dashboard.pastShelf).toHaveAttribute('aria-expanded', 'false');
    await expect(dashboard.pastShelf).toContainText('(3)');
    await expect(dashboard.pastShelf).toContainText('14–30 days old');
    await expect(dashboard.pastShelfRows).toHaveCount(0);
  });

  test('expanding lists the jobs with their age', async ({ dashboard, api }) => {
    api.setJobs(staleJobs(3));
    await dashboard.goto();
    await dashboard.pastShelf.click();

    await expect(dashboard.pastShelf).toHaveAttribute('aria-expanded', 'true');
    await expect(dashboard.pastShelfRows).toHaveCount(3);
    await expect(dashboard.pastShelfRows.first()).toContainText('Stale Role 0');
    await expect(dashboard.pastShelfRows.first()).toContainText('16 days old');
  });

  test('clicking a past job opens its workspace', async ({ dashboard, page, api }) => {
    api.setJobs(staleJobs(2));
    await dashboard.goto();
    await dashboard.pastShelf.click();
    await dashboard.pastShelfRows.filter({ hasText: 'Stale Role 1' }).click();
    await expect(page).toHaveURL(/\/jobs\/stale-1$/);
    await expect(page.getByRole('heading', { name: 'Stale Role 1' })).toBeVisible();
  });

  test('the shelf inherits the active filters', async ({ dashboard, api }) => {
    api.setJobs(staleJobs(3));
    await dashboard.goto();
    await expect(dashboard.pastShelf).toContainText('(3)');

    await dashboard.companySearch.fill('Stale Co 1');
    await dashboard.applyFilters.click();
    await expect(dashboard.pastShelf).toContainText('(1)');
  });

  test('the shelf stays mounted across a filter change instead of collapsing', async ({ dashboard, api }) => {
    // Deliberately not gated on the dashboard's `!loading`: gating it would
    // unmount and remount the shelf on every refetch, which both fires a
    // redundant request and silently collapses the shelf the moment the user
    // touches a filter.
    api.setJobs(staleJobs(3));
    await dashboard.goto();
    await dashboard.pastShelf.click();
    await expect(dashboard.pastShelfRows).toHaveCount(3);

    await dashboard.minScoreSlider.fill('70');
    await dashboard.applyFilters.click();
    await dashboard.waitForList();

    await expect(dashboard.pastShelf).toHaveAttribute('aria-expanded', 'true');
    await expect(dashboard.pastShelfRows).toHaveCount(3);
  });

  test('a shelf failure degrades to one line and leaves the main list alone', async ({ dashboard, api }) => {
    api.setJobs(staleJobs(3));
    api.override('/api/dashboard/jobs', ({ params }) => (
      params.lifecycle === 'stale' ? { status: 500, body: { detail: 'stale query blew up' } } : null
    ));
    await dashboard.goto();

    await expect(dashboard.page.getByText(/Couldn't load past \/ outdated jobs/)).toBeVisible();
    await expect(dashboard.heading).toBeVisible();
    await expect(dashboard.errorBanner).toHaveCount(0);
  });
});

test.describe('engaged jobs are exempt from ageing out', () => {
  test('an old Applied job is on the working list, not on the shelf', async ({ dashboard, api }) => {
    api.setJobs([
      makeJob({
        job_id: 'old-applied', title: 'Old Applied Role', company: 'Applied Co',
        match_score: 85, application_status: 'Applied', first_seen: daysAgo(90),
      }),
      ...staleJobs(1),
    ]);
    await dashboard.goto();

    await expect(dashboard.row('Applied Co')).toHaveCount(1);
    await dashboard.pastShelf.click();
    await expect(dashboard.pastShelfRows.filter({ hasText: 'Old Applied Role' })).toHaveCount(0);
  });
});
