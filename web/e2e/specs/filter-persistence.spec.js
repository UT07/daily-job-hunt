/**
 * Filters must survive a reload and a round trip through a job page.
 *
 * The dashboard holds its filter state in URL query params (`readFilterFromParams`
 * on the way in, a `setSearchParams` effect on the way out). That makes the
 * round trip observable, which is the only reason "state survives navigation"
 * is testable at all rather than a thing everyone assumes.
 */

import { test, expect, DEFAULT_LIST_PARAMS } from '../fixtures/test.js';

test.describe('URL <-> filter state', () => {
  test('the URL stays clean while every filter is at its default', async ({ dashboard, page }) => {
    await dashboard.goto();
    // Defaults are not written out: a one-click link from elsewhere in the app
    // should not arrive carrying six redundant params.
    expect(new URL(page.url()).search).toBe('');
  });

  test('changing filters writes them to the URL', async ({ dashboard, page }) => {
    await dashboard.goto();

    await dashboard.statusFilter.selectOption('Applied');
    await dashboard.sourceFilter.selectOption('linkedin');
    await dashboard.companySearch.fill('Echo');
    await dashboard.titleSearch.fill('Principal');
    await dashboard.applyFilters.click();
    await dashboard.waitForList();

    const params = dashboard.params();
    expect(params).toMatchObject({
      status: 'Applied',
      source: 'linkedin',
      company: 'Echo',
      title: 'Principal',
    });
  });

  test('a reload restores every control and re-sends every param', async ({ dashboard, page, api }) => {
    await dashboard.goto();

    await dashboard.statusFilter.selectOption('Applied');
    await dashboard.companySearch.fill('Echo');
    await dashboard.minScoreSlider.fill('80');
    await dashboard.hideExpiredToggle.click();
    await dashboard.tierTab('A').click();
    await dashboard.waitForList();

    const before = dashboard.params();
    api.reset();

    await dashboard.withListResponse(() => page.reload());
    await dashboard.waitForList();

    // 1. the URL survived
    expect(dashboard.params()).toEqual(before);
    // 2. the controls rehydrated from it
    await expect(dashboard.statusFilter).toHaveValue('Applied');
    await expect(dashboard.companySearch).toHaveValue('Echo');
    await expect(dashboard.minScoreSlider).toHaveValue('80');
    // 3. and the request the page made after reload carries them too --
    //    a control that displays a filter it is not actually applying is
    //    worse than one that forgets it.
    const req = api.lastListRequest();
    expect(req.params).toMatchObject({ status: 'Applied', company: 'Echo', min_score: '80', tier: 'A' });
    expect(req.params.hide_expired).toBeUndefined();
  });

  test('filters survive opening a job and coming back', async ({ dashboard, page, api }) => {
    await dashboard.goto();

    await dashboard.companySearch.fill('Aurora');
    await dashboard.applyFilters.click();
    await dashboard.waitForList();
    const filtered = dashboard.params();
    expect(filtered.company).toBe('Aurora');

    await dashboard.rows.first().locator('td').nth(2).click();
    await expect(page).toHaveURL(/\/jobs\//);
    // Wait for the job page to actually render, not just for the URL to change.
    // React Router renders the lazy JobWorkspace chunk inside a transition, so
    // the URL flips first while the dashboard stays mounted underneath until
    // the chunk resolves. Going back inside that window means the dashboard
    // never unmounted -- no refetch, nothing to assert, and a 30s hang waiting
    // for a request that is correctly never made.
    await expect(page.getByRole('button', { name: 'Overview' })).toBeVisible();

    api.reset();
    await dashboard.withListResponse(() => page.goBack());
    await dashboard.waitForList();

    expect(dashboard.params()).toEqual(filtered);
    await expect(dashboard.companySearch).toHaveValue('Aurora');
    expect(api.lastListRequest().params.company).toBe('Aurora');
  });

  test('a deep link with filters is honoured on first paint', async ({ dashboard, api }) => {
    await dashboard.goto('?tier=B&min_score=70&source=adzuna');

    await expect(dashboard.statusFilter).toHaveValue('All');
    await expect(dashboard.sourceFilter).toHaveValue('adzuna');
    await expect(dashboard.minScoreSlider).toHaveValue('70');

    const req = api.lastListRequest();
    expect(req.params).toMatchObject({ tier: 'B', min_score: '70', source: 'adzuna' });
    // Deep-linked or not, the rest of the defaults still apply.
    expect(req.params.hide_expired).toBe('true');
  });

  test('a deep link can turn Hide Expired off, and expired jobs come back', async ({ dashboard, api }) => {
    await dashboard.goto('?hide_expired=false');
    expect(api.lastListRequest().params.hide_expired).toBeUndefined();
    await expect(dashboard.row('Foxtrot Media')).toHaveCount(1);
  });

  test('"Clear all filters" resets the URL, the controls and the query', async ({ dashboard, page, api }) => {
    await dashboard.goto('?tier=S&company=NoSuchCompanyAnywhere&min_score=95');
    await expect(dashboard.emptyState).toBeVisible();

    await dashboard.clearAllButton.click();
    await dashboard.waitForList();

    await expect(dashboard.companySearch).toHaveValue('');
    const req = api.lastListRequest();
    expect(req.params.company).toBeUndefined();
    expect(req.params.tier).toBeUndefined();
    // Clear-all is deliberately more aggressive than "back to defaults": it
    // drops min_score to 0 and Hide Expired to off, because a user who clicks
    // it is saying "show me everything", not "show me the default slice".
    expect(req.params.min_score).toBeUndefined();
    expect(req.params.hide_expired).toBeUndefined();
    expect(new URL(page.url()).searchParams.get('tier')).toBeNull();
  });

  test('a single filter chip clears only its own filter', async ({ dashboard, api }) => {
    await dashboard.goto('?company=Aurora&tier=S');
    await dashboard.waitForList();

    await dashboard.filterChip('Company: "Aurora"').first().click();
    await dashboard.waitForList();

    await expect.poll(() => api.lastListRequest().params.company).toBeUndefined();
    expect(api.lastListRequest().params.tier).toBe('S');
  });

  test('the page number is part of the persisted state', async ({ dashboard, page, api }) => {
    await dashboard.goto('?page=2');
    expect(api.lastListRequest().params.page).toBe('2');
    expect(new URL(page.url()).searchParams.get('page')).toBe('2');
  });
});

test.describe('defaults', () => {
  test('a filter left at its default is not written to the URL', async ({ dashboard, page }) => {
    await dashboard.goto();
    await dashboard.statusFilter.selectOption('Applied');
    await dashboard.waitForList();
    await dashboard.statusFilter.selectOption('All');
    await dashboard.applyFilters.click();
    await dashboard.waitForList();
    expect(new URL(page.url()).searchParams.get('status')).toBeNull();
  });

  test('an explicit default in the URL behaves like the default', async ({ dashboard, api }) => {
    await dashboard.goto('?min_score=60&hide_expired=true&tier=All');
    expect(api.lastListRequest().params).toEqual(DEFAULT_LIST_PARAMS);
  });
});
