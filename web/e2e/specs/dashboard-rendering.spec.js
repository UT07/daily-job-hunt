/**
 * What the dashboard DOES with what it gets back.
 *
 * Every assertion here is about the app's own behaviour given a controlled
 * response: does it render the rows it was handed, does it dim the ones the
 * design says to dim, does it explain an empty list instead of just being
 * empty, and does it say something honest when the backend fails.
 */

import { test, expect } from '../fixtures/test.js';
import { makeJob, daysAgo } from '../fixtures/jobs.js';

test.describe('the job list', () => {
  test('renders one row per job returned, and no client-side re-filtering', async ({ dashboard, api }) => {
    await dashboard.goto();
    const served = api.queryJobs(api.lastListRequest().params).jobs;
    expect(served.length).toBeGreaterThan(0);
    await expect(dashboard.rows).toHaveCount(served.length);

    const titles = await dashboard.rowTitles();
    for (const job of served) {
      expect(titles.join(' | ')).toContain(job.title);
    }
  });

  test('shows title, company, location and score for a row', async ({ dashboard }) => {
    await dashboard.goto();
    const row = dashboard.row('Aurora Systems');
    await expect(row).toHaveCount(1);
    await expect(row).toContainText('Staff Platform Engineer');
    await expect(row).toContainText('Dublin, Ireland');
    await expect(row).toContainText('93');
  });

  test('the count line agrees with the number of rows', async ({ dashboard }) => {
    await dashboard.goto();
    const count = await dashboard.rows.count();
    await expect(dashboard.resultCount).toContainText(`${count} job`);
  });

  test('an expired job is badged EXPIRED', async ({ dashboard }) => {
    await dashboard.goto('?hide_expired=false');
    await expect(dashboard.row('Foxtrot Media').getByText('EXPIRED').first()).toBeVisible();
  });

  test('a rejected job is struck through', async ({ dashboard }) => {
    await dashboard.goto();
    const titleCell = dashboard.row('Iris Analytics').locator('td').nth(2);
    await expect(titleCell).toHaveClass(/line-through/);
  });

  test('the tier tab shows the count of the tier it is on', async ({ dashboard }) => {
    await dashboard.goto();
    await dashboard.tierTab('A').click();
    await dashboard.waitForList();
    const rows = await dashboard.rows.count();
    await expect(dashboard.tierTab('A')).toContainText(`(${rows})`);
  });
});

test.describe('the empty state', () => {
  test('explains which filters are hiding the jobs, and each is clearable', async ({ dashboard }) => {
    await dashboard.goto('?company=NoSuchCompanyAnywhere&tier=S');
    await expect(dashboard.emptyState).toBeVisible();
    // Not a bare "No jobs found": it names the filters responsible. A default
    // that hides 94.9% of the corpus has to be visible and one click from undone.
    await expect(dashboard.firstRunEmptyState).toHaveCount(0);
    await expect(dashboard.page.getByText('Company: "NoSuchCompanyAnywhere"').first()).toBeVisible();
    await expect(dashboard.page.getByText('Tier: Must Apply (S)').first()).toBeVisible();
    await expect(dashboard.clearAllButton).toBeVisible();
  });

});

test.describe('a genuinely empty account', () => {
  test('gets "No jobs yet", not "no jobs match your filters"', async ({ dashboard, api }) => {
    api.setJobs([]);
    await dashboard.goto();
    await expect(dashboard.firstRunEmptyState).toBeVisible();
    await expect(dashboard.emptyState).toHaveCount(0);
  });
});

test.describe('backend failure', () => {
  test('a 503 is surfaced as an error, not as an empty dashboard', async ({ dashboard, page }) => {
    // require_db returns 503 when the database is unreachable. Before that
    // dependency existed the endpoint returned an empty success payload, and
    // "the database is down" and "you have no jobs" looked identical.
    await page.route('**/api/dashboard/jobs**', (route) => route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Database not configured' }),
    }));
    await page.goto('/');
    await expect(dashboard.heading).toBeVisible();
    await expect(page.getByText('Database not configured').first()).toBeVisible();
  });

  test('a FastAPI 422 validation array is rendered readably, not as [object Object]', async ({ dashboard, page }) => {
    await page.route('**/api/dashboard/jobs**', (route) => route.fulfill({
      status: 422,
      contentType: 'application/json',
      body: JSON.stringify({
        detail: [{ loc: ['body', 'min_score'], msg: 'value is not a valid float', type: 'type_error.float' }],
      }),
    }));
    await page.goto('/');
    await expect(page.getByText('min_score: value is not a valid float').first()).toBeVisible();
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });
});

test.describe('row actions', () => {
  test('changing a status PATCHes the job and updates the row', async ({ dashboard, api, page }) => {
    await dashboard.goto();
    const row = dashboard.row('Aurora Systems');
    await row.getByRole('button', { name: 'New' }).click();
    await row.getByRole('button', { name: 'Applied', exact: true }).click();

    await expect.poll(() => api.requestsFor('/api/dashboard/jobs/job-s-tier').filter((r) => r.method === 'PATCH').length)
      .toBe(1);
    const patch = api.requestsFor('/api/dashboard/jobs/job-s-tier').find((r) => r.method === 'PATCH');
    expect(patch.body).toEqual({ application_status: 'Applied' });
    await expect(row.getByRole('button', { name: /APPLIED/i })).toBeVisible();
  });

  test('deleting a job removes the row and decrements the count', async ({ dashboard, api }) => {
    await dashboard.goto();
    const before = await dashboard.rows.count();
    const row = dashboard.row('Cygnus Cloud');

    await row.getByTitle('Delete job').click();
    await row.getByRole('button', { name: 'Yes', exact: true }).click();

    await expect(dashboard.rows).toHaveCount(before - 1);
    expect(api.requestsFor('/api/dashboard/jobs/job-b-tier').some((r) => r.method === 'DELETE')).toBe(true);
    await expect(dashboard.resultCount).toContainText(`${before - 1} job`);
  });

  test('a failed delete leaves the row in place and flags the button', async ({ dashboard, api }) => {
    await dashboard.goto();
    const before = await dashboard.rows.count();
    api.override('/api/dashboard/jobs/job-b-tier', { status: 500, body: { detail: 'boom' } });

    const row = dashboard.row('Cygnus Cloud');
    await row.getByTitle('Delete job').click();
    await row.getByRole('button', { name: 'Yes', exact: true }).click();

    await expect(dashboard.rows).toHaveCount(before);
    await expect(row.getByTitle(/Delete failed/)).toBeVisible();
  });
});

test.describe('view mode', () => {
  test('card view survives a reload', async ({ dashboard, page }) => {
    await dashboard.goto();
    await page.getByTitle('Card view').click();
    await expect(dashboard.table).toHaveCount(0);

    await dashboard.withListResponse(() => page.reload());
    await expect(dashboard.table).toHaveCount(0);
    await expect(page.getByText('Staff Platform Engineer').first()).toBeVisible();
  });
});

test.describe('the hidden-jobs count', () => {
  test('says how many non-expired jobs the filters are hiding', async ({ dashboard, api }) => {
    api.setJobs([
      makeJob({ job_id: 'visible-1', title: 'Visible One', company: 'Vis Co', match_score: 90, first_seen: daysAgo(1) }),
      makeJob({ job_id: 'hidden-low', title: 'Hidden Low', company: 'Low Co', match_score: 20, first_seen: daysAgo(1) }),
      makeJob({ job_id: 'hidden-low-2', title: 'Hidden Low Two', company: 'Low Co', match_score: 21, first_seen: daysAgo(1) }),
    ]);
    await dashboard.goto();
    await expect(dashboard.rows).toHaveCount(1);
    // 3 non-expired jobs exist; min_score=60 leaves 1 on screen.
    await expect(dashboard.resultCount).toContainText('2 more non-expired jobs hidden by filters');
  });
});
