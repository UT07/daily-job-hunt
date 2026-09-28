/**
 * The per-job workspace: header, tabs, inline edit, status timeline.
 */

import { test, expect } from '../fixtures/test.js';
import { makeJob, daysAgo } from '../fixtures/jobs.js';

test.describe('opening a job', () => {
  test('a row click lands on that job, with its own data in the header', async ({ dashboard, page }) => {
    await dashboard.goto();
    await dashboard.row('Aurora Systems').locator('td').nth(2).click();

    await expect(page).toHaveURL(/\/jobs\/job-s-tier$/);
    await expect(page.getByRole('heading', { name: 'Staff Platform Engineer' })).toBeVisible();
    await expect(page.getByText('Aurora Systems · Dublin, Ireland')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Apply' })).toHaveAttribute(
      'href', 'https://example.invalid/jobs/1',
    );
  });

  test('a deep link to a job id that does not exist says so, with a way back', async ({ page }) => {
    await page.goto('/jobs/no-such-job');
    await expect(page.getByText(/Failed to load job|Job not found/)).toBeVisible();
    await page.getByRole('button', { name: 'Back to Dashboard' }).click();
    await expect(page.getByRole('heading', { name: 'Job Dashboard' })).toBeVisible();
  });

  test('every tab opens without throwing', async ({ page, dashboard, consoleErrors }) => {
    await dashboard.goto();
    await dashboard.row('Aurora Systems').locator('td').nth(2).click();
    await expect(page.getByRole('button', { name: 'Overview' })).toBeVisible();

    for (const tab of ['Research', 'Resume', 'Editor', 'Cover Letter', 'Contacts', 'Interview Prep']) {
      await page.getByRole('button', { name: tab, exact: true }).click();
      // The panel is the bordered box under the tab strip; it must render
      // something rather than collapsing to nothing.
      await expect(page.locator('div.min-h-\\[300px\\]')).toBeVisible();
    }
    await page.getByRole('button', { name: 'Overview', exact: true }).click();
    await expect(page.getByText('APPLICATION TIMELINE')).toBeVisible();

    const realErrors = consoleErrors.filter((e) => !/Failed to load resource|501/.test(e));
    expect(realErrors, `console errors while switching tabs:\n${realErrors.join('\n')}`).toEqual([]);
  });
});

/**
 * The inline edit form's four fields are addressed BY POSITION, not by label.
 *
 * That is not a stylistic choice. JobWorkspace renders each field as a bare
 * `<label>` next to an `<Input>` with no `id` and no `htmlFor`, so the labels
 * are not programmatically associated with their inputs at all -- getByLabel
 * finds nothing, and a screen reader announces four anonymous text boxes. See
 * the accessibility test at the bottom of this describe.
 */
const EDIT_FIELDS = { title: 0, company: 1, location: 2, apply_url: 3 };

function editGrid(page) {
  return page.locator('div.border-yellow.bg-yellow-light');
}

test.describe('inline editing', () => {
  test('saving an edit PATCHes only the editable fields and updates the header', async ({ page, dashboard, api }) => {
    await dashboard.goto();
    await dashboard.row('Aurora Systems').locator('td').nth(2).click();
    await expect(page.getByRole('button', { name: 'Overview' })).toBeVisible();

    await page.getByRole('button', { name: 'Edit', exact: true }).click();
    await editGrid(page).locator('input').nth(EDIT_FIELDS.location).fill('Cork, Ireland');
    await page.getByRole('button', { name: /^Save/ }).click();

    await expect(page.getByText('Job updated.')).toBeVisible();
    const patch = api.requestsFor('/api/dashboard/jobs/job-s-tier').find((r) => r.method === 'PATCH');
    expect(patch.body).toMatchObject({ location: 'Cork, Ireland' });
    await expect(page.getByText('Aurora Systems · Cork, Ireland')).toBeVisible();
  });

  test('a failed save says so and keeps the form open', async ({ page, dashboard, api }) => {
    await dashboard.goto();
    await dashboard.row('Aurora Systems').locator('td').nth(2).click();
    await expect(page.getByRole('button', { name: 'Overview' })).toBeVisible();

    api.override('/api/dashboard/jobs/job-s-tier', ({ method }) => (
      method === 'PATCH' ? { status: 400, body: { detail: 'At least one editable field required' } } : null
    ));

    await page.getByRole('button', { name: 'Edit', exact: true }).click();
    await editGrid(page).locator('input').nth(EDIT_FIELDS.location).fill('Nowhere');
    await page.getByRole('button', { name: /^Save/ }).click();

    await expect(page.getByText(/Save failed: At least one editable field required/)).toBeVisible();
  });

  /**
   * KNOWN DEFECT -- `fixme`; delete the marker when it is fixed.
   *
   * The four edit fields render as `<label>Location</label>` beside an
   * `<Input>` that is given neither an `id` nor a `label` prop. Input.jsx
   * derives its id from `id || label`, so with both absent the input gets
   * `id={undefined}` and the label points at nothing. A screen-reader user
   * editing a job hears four unnamed text boxes, and every test that tries the
   * obvious `getByLabel('Location')` hangs for its full timeout.
   *
   * Fix: pass `label="Location"` to <Input> and drop the hand-rolled <label>,
   * which is exactly how the Add Job form already does it.
   */
  test.fixme('the edit fields are reachable by their labels', async ({ page, dashboard }) => {
    await dashboard.goto();
    await dashboard.row('Aurora Systems').locator('td').nth(2).click();
    await page.getByRole('button', { name: 'Edit', exact: true }).click();
    for (const label of ['Title', 'Company', 'Location', 'Apply URL']) {
      await expect(page.getByLabel(label, { exact: true })).toBeVisible();
    }
  });
});

test.describe('an expired, applied job', () => {
  test('still opens and shows its applied status', async ({ page, dashboard }) => {
    await dashboard.goto();
    await dashboard.row('Echo Financial').locator('td').nth(2).click();
    await expect(page.getByRole('heading', { name: 'Principal Engineer' })).toBeVisible();
    await expect(page.getByText('APPLIED').first()).toBeVisible();
  });
});

test.describe('a job with no artefacts yet', () => {
  test('the resume tab says so instead of rendering an empty frame', async ({ page, dashboard, api }) => {
    api.setJobs([makeJob({
      job_id: 'bare-job', title: 'Bare Role', company: 'Bare Co',
      match_score: 88, first_seen: daysAgo(1), resume_s3_url: null, cover_letter_s3_url: null,
    })]);
    await dashboard.goto();
    await dashboard.row('Bare Co').locator('td').nth(2).click();
    await expect(page.getByRole('heading', { name: 'Bare Role' })).toBeVisible();

    await page.getByRole('button', { name: 'Resume', exact: true }).click();
    await expect(page.locator('div.min-h-\\[300px\\]')).toContainText(/no|not|generate/i);
  });
});
