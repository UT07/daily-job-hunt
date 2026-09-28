/**
 * LIVE: the browser, the real backend, the real database.
 *
 * Deliberately short. The exhaustive UI work is in e2e/specs (fast, hermetic,
 * no credentials); what only this file can prove is that the whole chain --
 * browser -> Vite proxy -> FastAPI -> Supabase -> back -> React -- agrees with
 * itself. Everything here is scoped to the synthetic user's nine seeded rows.
 */

import { test, expect } from '@playwright/test';
import { Dashboard } from '../fixtures/dashboard.js';

test.describe('the real stack, end to end', () => {
  test('the dashboard renders the seeded jobs and nobody else\'s', async ({ page }) => {
    const dashboard = new Dashboard(page);
    await dashboard.goto();

    const titles = (await dashboard.rowTitles()).join(' | ');
    expect(titles).toContain('Staff Platform Engineer');
    expect(titles).toContain('Senior Backend Engineer');
    // The default min_score of 60 keeps the 41-scorer off the board.
    expect(titles).not.toContain('Junior Support Analyst');
    // Age: 140 days and unengaged.
    expect(titles).not.toContain('Ancient Posting');

    for (const company of await dashboard.rowCompanies()) {
      expect(company, 'a row from another tenant reached the dashboard').toContain('E2E');
    }
  });

  test('bug 2, through the whole stack: the applied job is on the default board', async ({ page }) => {
    const dashboard = new Dashboard(page);
    await dashboard.goto();
    const row = dashboard.row('Echo Financial E2E');
    await expect(row).toHaveCount(1);
    await expect(row.getByText('EXPIRED').first()).toBeVisible();
    await expect(row.getByRole('button', { name: /Applied/i })).toBeVisible();
  });

  test('bug 3, through the whole stack: the 140-day posting is on no list', async ({ page }) => {
    const dashboard = new Dashboard(page);
    await dashboard.goto('?min_score=0&hide_expired=false');
    await expect(dashboard.row('Helios Legacy E2E')).toHaveCount(0);
    if (await dashboard.pastShelf.count()) {
      await dashboard.pastShelf.click();
      await expect(dashboard.pastShelfRows.filter({ hasText: 'Ancient Posting' })).toHaveCount(0);
    }
  });

  test('the Past / Outdated shelf shows the real stale row', async ({ page }) => {
    const dashboard = new Dashboard(page);
    await dashboard.goto();
    await expect(dashboard.pastShelf).toBeVisible();
    await dashboard.pastShelf.click();
    await expect(dashboard.pastShelfRows.filter({ hasText: 'Infrastructure Engineer' })).toHaveCount(1);
  });

  test('a filter round trip survives a reload against real data', async ({ page }) => {
    const dashboard = new Dashboard(page);
    await dashboard.goto();
    await dashboard.companySearch.fill('Aurora');
    await dashboard.withListResponse(() => dashboard.applyFilters.click());
    await expect.poll(async () => (await dashboard.rowCompanies()).join()).toBe('Aurora Systems E2E');

    await dashboard.withListResponse(() => page.reload());
    await dashboard.waitForList();
    await expect(dashboard.companySearch).toHaveValue('Aurora');
    await expect.poll(async () => (await dashboard.rowCompanies()).join()).toBe('Aurora Systems E2E');
  });

  test('opening a job loads it from the database', async ({ page }) => {
    const dashboard = new Dashboard(page);
    await dashboard.goto();
    await dashboard.row('Aurora Systems E2E').locator('td').nth(2).click();
    await expect(page).toHaveURL(/\/jobs\/e2e-live-s-tier$/);
    await expect(page.getByRole('heading', { name: 'Staff Platform Engineer' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Overview' })).toBeVisible();
  });

  test('a status change written from the table survives a reload', async ({ page }) => {
    const dashboard = new Dashboard(page);
    await dashboard.goto();
    const row = dashboard.row('Cygnus Cloud E2E');
    try {
      await row.getByRole('button', { name: 'New' }).click();
      await row.getByRole('button', { name: 'Interview', exact: true }).click();
      await expect(row.getByRole('button', { name: /Interview/i })).toBeVisible();

      await dashboard.withListResponse(() => page.reload());
      await dashboard.waitForList();
      await expect(dashboard.row('Cygnus Cloud E2E').getByRole('button', { name: /Interview/i }))
        .toBeVisible();
    } finally {
      // Put the fixture back so the next run starts from a known state.
      const back = dashboard.row('Cygnus Cloud E2E');
      await back.getByRole('button', { name: /Interview/i }).click();
      await back.getByRole('button', { name: 'New', exact: true }).click();
    }
  });
});
