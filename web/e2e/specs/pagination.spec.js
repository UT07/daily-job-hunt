/**
 * Pagination, and the page-number state that hangs off it.
 *
 * Page number is filter state like any other: it lives in the URL, it has to
 * survive a reload, and -- the part that is actually broken -- it has to reset
 * when the result set changes underneath it.
 */

import { test, expect } from '../fixtures/test.js';
import { manyJobs } from '../fixtures/jobs.js';

test.describe('60 jobs, 25 to a page', () => {
  test('the first page holds 25 rows and the count is the full total', async ({ dashboard, api }) => {
    api.setJobs(manyJobs(60));
    await dashboard.goto();
    await expect(dashboard.rows).toHaveCount(25);
    await expect(dashboard.resultCount).toContainText('60 jobs');
    expect(api.lastListRequest().params).toMatchObject({ page: '1', per_page: '25' });
  });

  test('Next asks for page 2 and shows the next 25', async ({ dashboard, api }) => {
    api.setJobs(manyJobs(60));
    await dashboard.goto();
    const firstPage = await dashboard.rowTitles();

    await dashboard.withListResponse(() => dashboard.nextPage.click());
    await expect.poll(async () => (await dashboard.rowTitles())[0]).not.toBe(firstPage[0]);

    expect(api.lastListRequest().params.page).toBe('2');
    const secondPage = await dashboard.rowTitles();
    expect(secondPage).toHaveLength(25);
    // No overlap: a page that re-serves rows you have already seen is the
    // classic off-by-one in an offset/limit pair.
    expect(secondPage.filter((t) => firstPage.includes(t))).toEqual([]);
  });

  test('the last page has the remainder and Next is disabled there', async ({ dashboard, api }) => {
    api.setJobs(manyJobs(60));
    await dashboard.goto();
    await dashboard.withListResponse(() => dashboard.pageButton(3).click());
    await expect.poll(() => dashboard.rows.count()).toBe(10);
    await expect(dashboard.nextPage).toBeDisabled();
  });

  test('Prev and First are disabled on page 1', async ({ dashboard, api }) => {
    api.setJobs(manyJobs(60));
    await dashboard.goto();
    await expect(dashboard.prevPage).toBeDisabled();
    await expect(dashboard.firstPage).toBeDisabled();
  });

  test('the current page survives a reload', async ({ dashboard, page, api }) => {
    api.setJobs(manyJobs(60));
    await dashboard.goto();
    await dashboard.withListResponse(() => dashboard.pageButton(2).click());
    await expect.poll(() => new URL(page.url()).searchParams.get('page')).toBe('2');

    const onPageTwo = await dashboard.rowTitles();
    await dashboard.withListResponse(() => page.reload());
    await expect.poll(async () => (await dashboard.rowTitles()).join()).toBe(onPageTwo.join());
  });

  test('applying a filter returns to page 1', async ({ dashboard, api }) => {
    api.setJobs(manyJobs(60));
    await dashboard.goto();
    await dashboard.withListResponse(() => dashboard.pageButton(3).click());
    await expect.poll(() => api.lastListRequest().params.page).toBe('3');

    await dashboard.companySearch.fill('Bulk Co 0');
    await dashboard.withListResponse(() => dashboard.applyFilters.click());
    await expect.poll(() => api.lastListRequest().params.page).toBe('1');
  });

  test('changing the sort returns to page 1', async ({ dashboard, api }) => {
    api.setJobs(manyJobs(60));
    await dashboard.goto();
    await dashboard.withListResponse(() => dashboard.pageButton(3).click());
    await expect.poll(() => api.lastListRequest().params.page).toBe('3');

    await dashboard.withListResponse(() => dashboard.sortSelect.selectOption('match_score:desc'));
    await expect.poll(() => api.lastListRequest().params.page).toBe('1');
  });

  /**
   * KNOWN DEFECT -- `test.fail`; delete the marker once it is fixed.
   *
   * Every other way of narrowing the list goes through handleFilterApply(),
   * which does setPage(1). The tier tabs do not: their onClick is
   * `setTierFilter(...); setFilterVersion(v => v + 1)` and nothing else. So
   * clicking "Must Apply" from page 3 asks for page 3 of a 12-row result set
   * and the user gets the "No jobs match your current filters" empty state
   * for a tier that has twelve jobs in it -- on a dashboard whose whole
   * redesign was about not looking broken when it is merely filtered.
   *
   * Repro: 60 jobs, go to page 3, click any tier tab.
   * Expected: page 1 of that tier.  Observed: page 3, empty.
   * Fix: add setPage(1) to the tier tab onClick in Dashboard.jsx.
   */
  test.fail('switching tier tabs returns to page 1', async ({ dashboard, api }) => {
    api.setJobs(manyJobs(60));
    await dashboard.goto();
    await dashboard.withListResponse(() => dashboard.pageButton(3).click());
    await expect.poll(() => api.lastListRequest().params.page).toBe('3');

    await dashboard.withListResponse(() => dashboard.tierTab('S').click());
    await expect.poll(() => api.lastListRequest().params.page, { timeout: 3_000 }).toBe('1');
    await expect(dashboard.rows).not.toHaveCount(0);
  });
});
