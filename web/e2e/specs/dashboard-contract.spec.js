/**
 * What the dashboard ASKS FOR.
 *
 * This is the half of the filter story a mock can prove: not "does the backend
 * interpret hide_expired correctly" (that is e2e/live/api.spec.js) but "does
 * the app actually send it, with these defaults, every time". Three of the
 * five bugs that prompted this suite were a default or a param in the wrong
 * place, and every one of them would show up here as a changed query string.
 *
 * These assertions are deliberately exact. A loose `toContain('hide_expired')`
 * would let a silently-dropped min_score through.
 */

import { test, expect, DEFAULT_LIST_PARAMS } from '../fixtures/test.js';

test.describe('the first request of a fresh session', () => {
  test('sends exactly the documented default filter set', async ({ dashboard, api }) => {
    await dashboard.goto();

    const req = api.lastListRequest();
    expect(req, 'the dashboard made no list request at all').toBeTruthy();
    expect(req.params).toEqual(DEFAULT_LIST_PARAMS);
  });

  test('does NOT send a lifecycle param — the backend default is the contract', async ({ dashboard, api }) => {
    // jobQuery.js deliberately omits it: "the backend's own default is
    // not_archived, and spelling it out here would mean two places to change".
    // If a future edit starts sending lifecycle=all from the active list, the
    // 1,164 archived rows come back and the dashboard is unreadable again.
    await dashboard.goto();
    expect(api.lastListRequest().params).not.toHaveProperty('lifecycle');
  });

  test('sends a separate unfiltered probe for the absolute job count', async ({ dashboard, api }) => {
    await dashboard.goto();
    const probes = api.requestsFor('/api/dashboard/jobs').filter((r) => r.params.per_page === '1');
    expect(probes.length).toBeGreaterThan(0);
    // Unfiltered on purpose: it exists to tell "brand new user, no jobs" apart
    // from "63 jobs hidden by filters", which it cannot do if it inherits the
    // filters doing the hiding.
    expect(probes[0].params).toEqual({ page: '1', per_page: '1' });
  });

  test('asks for stats and the skills list on load', async ({ dashboard, api }) => {
    await dashboard.goto();
    // Counts are bounded, not exact: React StrictMode double-invokes effects
    // on a dev server, so every effect-driven fetch legitimately fires twice
    // here and once in a production build. Asserting "exactly 1" would encode
    // the dev/prod difference into the suite; asserting "<= 2" still catches a
    // genuine refetch loop.
    expect(api.requestsFor('/api/dashboard/stats').length).toBeGreaterThan(0);
    expect(api.requestsFor('/api/dashboard/stats').length).toBeLessThanOrEqual(2);
    expect(api.requestsFor('/api/dashboard/skills').length).toBeGreaterThan(0);
    expect(api.requestsFor('/api/dashboard/skills').length).toBeLessThanOrEqual(2);
  });
});

test.describe('the Past / Outdated shelf', () => {
  test('asks for lifecycle=stale and inherits every other active filter', async ({ dashboard, api }) => {
    await dashboard.goto();

    const stale = api.staleRequests();
    expect(stale.length).toBeGreaterThan(0);

    const { lifecycle, ...rest } = stale[stale.length - 1].params;
    expect(lifecycle).toBe('stale');
    // Same query, differing only in age band. If the two drift, "27 jobs
    // hidden" stops adding up against what is on screen -- which is the exact
    // reason buildJobQueryParams was extracted in the first place.
    expect(rest).toEqual(DEFAULT_LIST_PARAMS);
  });

  test('re-queries with the new filters when a filter is applied', async ({ dashboard, api }) => {
    await dashboard.goto();
    const before = api.staleRequests().length;

    await dashboard.companySearch.fill('Aurora');
    await dashboard.applyFilters.click();
    await expect.poll(() => api.staleRequests().length).toBeGreaterThan(before);

    const latest = api.staleRequests().at(-1);
    expect(latest.params.company).toBe('Aurora');
    expect(latest.params.lifecycle).toBe('stale');
  });
});

test.describe('each control maps to its own query param', () => {
  const cases = [
    {
      name: 'Status',
      act: async (d) => { await d.statusFilter.selectOption('Applied'); await d.applyFilters.click(); },
      expected: { status: 'Applied' },
    },
    {
      name: 'Source',
      act: async (d) => { await d.sourceFilter.selectOption('linkedin'); await d.applyFilters.click(); },
      expected: { source: 'linkedin' },
    },
    {
      name: 'Company',
      act: async (d) => { await d.companySearch.fill('Aurora'); await d.applyFilters.click(); },
      expected: { company: 'Aurora' },
    },
    {
      name: 'Title',
      act: async (d) => { await d.titleSearch.fill('Platform'); await d.applyFilters.click(); },
      expected: { title: 'Platform' },
    },
    {
      name: 'Tailored',
      act: async (d) => { await d.tailoredToggle.click(); await d.applyFilters.click(); },
      expected: { tailored: 'true' },
    },
    {
      name: 'Tier tab',
      act: async (d) => { await d.tierTab('A').click(); },
      expected: { tier: 'A' },
    },
  ];

  for (const { name, act, expected } of cases) {
    test(`${name} reaches the API`, async ({ dashboard, api }) => {
      await dashboard.goto();
      const before = api.listRequests().length;
      await act(dashboard);
      await expect.poll(() => api.listRequests().length).toBeGreaterThan(before);
      expect(api.lastListRequest().params).toMatchObject(expected);
    });
  }

  test('Hide Expired off drops the API param but records false in the URL', async ({ dashboard, page, api }) => {
    await dashboard.goto();
    await dashboard.hideExpiredToggle.click();
    await dashboard.applyFilters.click();

    // The API param is omitted (the backend only acts on the literal "true"),
    // while the URL records hide_expired=false -- it has to, or a reload would
    // silently restore the default and re-hide the expired jobs the user just
    // asked to see. The two spellings are deliberate, not a drift.
    await expect.poll(() => api.lastListRequest().params.hide_expired).toBeUndefined();
    expect(new URL(page.url()).searchParams.get('hide_expired')).toBe('false');
  });

  test('Min Score 0 drops the param rather than sending 0', async ({ dashboard, page, api }) => {
    await dashboard.goto();
    await dashboard.minScoreSlider.fill('0');
    await dashboard.applyFilters.click();
    await expect.poll(() => api.lastListRequest().params.min_score).toBeUndefined();
    expect(new URL(page.url()).searchParams.get('min_score')).toBe('0');
  });

  test('Sort changes both sort_by and sort_order', async ({ dashboard, api }) => {
    await dashboard.goto();
    await dashboard.sortSelect.selectOption('match_score:asc');
    await expect.poll(() => api.lastListRequest().params.sort_by).toBe('match_score');
    expect(api.lastListRequest().params.sort_order).toBe('asc');
  });

  test('advanced filters (archetype, seniority, remote, level fit, skill) reach the API', async ({ dashboard, api }) => {
    await dashboard.goto();
    await dashboard.advancedToggle.click();

    const select = (label) => dashboard.page.getByLabel(label, { exact: false });
    await select('Archetype').selectOption('backend');
    await expect.poll(() => api.lastListRequest().params.archetype).toBe('backend');

    await select('Seniority').selectOption('Senior');
    await expect.poll(() => api.lastListRequest().params.seniority).toBe('Senior');

    await select('Remote').selectOption('Remote');
    await expect.poll(() => api.lastListRequest().params.remote).toBe('Remote');

    await select('Level Fit').selectOption('stretch');
    await expect.poll(() => api.lastListRequest().params.level_fit).toBe('stretch');

    await dashboard.page.getByPlaceholder('Type to search...').fill('Python');
    await expect.poll(() => api.lastListRequest().params.skill).toBe('Python');
  });
});
