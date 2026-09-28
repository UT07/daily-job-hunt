/**
 * Do the filter dropdowns offer values that actually exist?
 *
 * A filter option that can never match anything is not a cosmetic problem: the
 * user picks it, the board empties, and the app looks broken. This file pins
 * the option lists to the values the rest of the system agrees on.
 */

import { test, expect } from '../fixtures/test.js';
import { makeJob, daysAgo } from '../fixtures/jobs.js';

/** app.py `_VALID_STATUSES` — the only values PATCH /api/dashboard/jobs/{id}
 *  will accept, and therefore the only values `application_status` can hold. */
const BACKEND_STATUSES = [
  'New', 'Applied', 'Phone Screen', 'Interview', 'Offer', 'Rejected', 'Withdrawn', 'Accepted',
];

test.describe('the Status filter', () => {
  test('selecting a status filters to it', async ({ dashboard, api }) => {
    api.setJobs([
      makeJob({ job_id: 'st-new', company: 'New Co', application_status: 'New', match_score: 85, first_seen: daysAgo(1) }),
      makeJob({ job_id: 'st-applied', company: 'Applied Co', application_status: 'Applied', match_score: 85, first_seen: daysAgo(1) }),
    ]);
    await dashboard.goto();
    await expect(dashboard.rows).toHaveCount(2);

    await dashboard.statusFilter.selectOption('Applied');
    await dashboard.withListResponse(() => dashboard.applyFilters.click());
    await expect.poll(async () => (await dashboard.rowCompanies()).join()).toBe('Applied Co');
  });

  /**
   * KNOWN DEFECT -- `test.fail`; delete the marker when it is fixed.
   *
   * Dashboard.jsx's STATUS_OPTIONS is:
   *   All, New, Applied, Interview, Offer, Rejected, Withdrawn, Expired
   *
   * Two problems, both user-visible:
   *
   * 1. "Expired" is not a status. Expiry is `is_expired`, a different column,
   *    already exposed as its own Hide Expired toggle. Selecting it sends
   *    `status=Expired`, the backend runs
   *    `.eq("application_status", "Expired")`, and it matches zero rows --
   *    forever, for everyone. The dashboard empties and explains nothing
   *    beyond "Status: Expired".
   *
   * 2. "Phone Screen" and "Accepted" ARE real statuses -- the inline
   *    StatusDropdown sets them and app.py accepts them -- but there is no way
   *    to filter for either. You can mark a job Accepted and then never find
   *    it again by status.
   *
   * Fix: make STATUS_OPTIONS ['All', ...BACKEND_STATUSES]. The three lists
   * (StatusDropdown.STATUSES, JobWorkspace.VALID_STATUSES, app.py
   * _VALID_STATUSES) already agree with each other; only this one drifted.
   */
  test.fail('offers exactly the statuses the backend accepts', async ({ dashboard }) => {
    await dashboard.goto();
    const options = await dashboard.statusFilter.locator('option').allTextContents();
    expect(options).toEqual(['All', ...BACKEND_STATUSES]);
  });

  test('the "Expired" option matches nothing — documenting the defect above', async ({ dashboard, api }) => {
    api.setJobs([
      makeJob({ job_id: 'x1', company: 'Expired Co', is_expired: true, match_score: 85, first_seen: daysAgo(1) }),
    ]);
    await dashboard.goto('?hide_expired=false');
    await expect(dashboard.rows).toHaveCount(1);

    await dashboard.statusFilter.selectOption('Expired');
    await dashboard.withListResponse(() => dashboard.applyFilters.click());

    // An expired job is on the board, the user picks the option named after
    // that exact condition, and gets nothing.
    await expect(dashboard.emptyState).toBeVisible();
    expect(api.lastListRequest().params.status).toBe('Expired');
  });
});

test.describe('the Source filter', () => {
  test('every option is a source the pipeline can actually produce', async ({ dashboard }) => {
    // 'yc', 'hn' and 'web' were stale options matching zero rows anywhere; the
    // real value is 'hn_hiring'. Pinned so the list cannot silently rot again.
    await dashboard.goto();
    const options = await dashboard.sourceFilter.locator('option').allTextContents();
    expect(options).toEqual([
      'All', 'adzuna', 'linkedin', 'irishjobs', 'jobs_ie', 'gradireland',
      'hn_hiring', 'glassdoor', 'greenhouse', 'ashby', 'indeed', 'manual',
    ]);
    expect(options).not.toContain('yc');
    expect(options).not.toContain('hn');
  });
});
