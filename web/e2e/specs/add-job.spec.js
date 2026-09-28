/**
 * Add Job — the on-demand "paste a JD, get artefacts" flow the product has
 * pivoted to. Four buttons, two of which go through Step Functions.
 */

import { test, expect } from '../fixtures/test.js';

const A_REAL_JD = [
  'Senior Platform Engineer at a payments company.',
  'You will own the Kubernetes estate, write Terraform, and keep the AWS bill honest.',
  'Python across the stack, plus Go where latency matters.',
  'Five years of experience running distributed systems in production.',
].join(' ');

test.describe('the form', () => {
  test('every action is disabled until a job description is pasted', async ({ page }) => {
    await page.goto('/add-job');
    for (const name of ['Save & Score', 'Tailor Resume', 'Cover Letter', 'Find Contacts']) {
      await expect(page.getByRole('button', { name })).toBeDisabled();
    }
    await page.getByLabel('Job Description').fill(A_REAL_JD);
    for (const name of ['Save & Score', 'Tailor Resume', 'Cover Letter', 'Find Contacts']) {
      await expect(page.getByRole('button', { name })).toBeEnabled();
    }
  });

  test('a suspiciously short JD is called out before it is scored', async ({ page }) => {
    await page.goto('/add-job');
    await page.getByLabel('Job Description').fill('Engineer wanted.');
    await expect(page.getByText(/Job description seems too short/)).toBeVisible();

    await page.getByLabel('Job Description').fill(A_REAL_JD);
    await expect(page.getByText(/Job description seems too short/)).toHaveCount(0);
  });

  test('only one action can run at a time', async ({ page, api }) => {
    api.override('/api/score', async () => {
      await new Promise((r) => setTimeout(r, 1500));
      return { status: 200, body: api.handleScore({ company: 'Slow Co' }) };
    });
    await page.goto('/add-job');
    await page.getByLabel('Job Description').fill(A_REAL_JD);
    await page.getByRole('button', { name: 'Save & Score' }).click();

    await expect(page.getByRole('button', { name: 'Tailor Resume' })).toBeDisabled();
    await expect(page.getByText('Scoring job match...')).toBeVisible();
  });
});

test.describe('Save & Score', () => {
  test('renders all four scores and the reasoning', async ({ page }) => {
    await page.goto('/add-job');
    await page.getByLabel('Job Description').fill(A_REAL_JD);
    await page.getByLabel('Company').fill('Scorecard Co');
    await page.getByRole('button', { name: 'Save & Score' }).click();

    const card = page.locator('div').filter({ hasText: /^Score Card — Scorecard Co/ }).first();
    await expect(card).toBeVisible();
    for (const label of ['ATS', 'Hiring Mgr', 'Tech Recruiter', 'Average']) {
      await expect(card.getByText(label, { exact: true })).toBeVisible();
    }
    await expect(page.getByText('Scored by the E2E fake backend.')).toBeVisible();
  });

  test('a backend failure is shown, not swallowed', async ({ page, api }) => {
    api.override('/api/score', { status: 500, body: { detail: 'AI scoring failed: all providers down' } });
    await page.goto('/add-job');
    await page.getByLabel('Job Description').fill(A_REAL_JD);
    await page.getByRole('button', { name: 'Save & Score' }).click();

    await expect(page.getByText('AI scoring failed: all providers down')).toBeVisible();
    // ...and the button comes back, rather than staying stuck in a spinner.
    await expect(page.getByRole('button', { name: 'Save & Score' })).toBeEnabled();
  });

  test('the resume type chosen is the one sent', async ({ page, api }) => {
    await page.goto('/add-job');
    await page.getByLabel('Job Description').fill(A_REAL_JD);
    await page.getByLabel('Resume Type').selectOption('fullstack');
    await page.getByRole('button', { name: 'Save & Score' }).click();
    await expect(page.getByText(/Score Card/)).toBeVisible();
    expect(api.lastRequest('/api/score').body.resume_type).toBe('fullstack');
  });
});

test.describe('Tailor Resume (Step Functions)', () => {
  test('starts a single-job pipeline, polls it, and shows the PDF link', async ({ page, api }) => {
    await page.goto('/add-job');
    await page.getByLabel('Job Description').fill(A_REAL_JD);
    await page.getByLabel('Company').fill('Tailor Co');
    await page.getByRole('button', { name: 'Tailor Resume' }).click();

    await expect(page.getByText('Processing job...')).toBeVisible();
    // pollPipeline sleeps 5s before its first poll, so this legitimately takes
    // longer than a normal assertion window.
    await expect(page.getByTestId('tailor-card')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole('link', { name: /Download Resume PDF/ })).toHaveAttribute(
      'href', 'https://example.invalid/tailored.pdf',
    );

    expect(api.lastRequest('/api/pipeline/run-single').body).toMatchObject({ company: 'Tailor Co' });
  });

  test('a FAILED execution surfaces the failure instead of spinning forever', async ({ page, api }) => {
    api.pipelineResult = { executionName: 'e2e-exec', status: 'FAILED', error: 'States.TaskFailed' };
    await page.goto('/add-job');
    await page.getByLabel('Job Description').fill(A_REAL_JD);
    await page.getByRole('button', { name: 'Tailor Resume' }).click();
    await expect(page.getByText('States.TaskFailed')).toBeVisible({ timeout: 20_000 });
  });
});

test.describe('Cover Letter (Step Functions)', () => {
  test('produces a downloadable cover letter', async ({ page, api }) => {
    api.pipelineResult = {
      executionName: 'e2e-exec',
      status: 'SUCCEEDED',
      output: { pdf_url: 'https://example.invalid/cover.pdf' },
    };
    await page.goto('/add-job');
    await page.getByLabel('Job Description').fill(A_REAL_JD);
    await page.getByLabel('Company').fill('Letter Co');
    await page.getByRole('button', { name: 'Cover Letter' }).click();

    await expect(page.getByText('Cover Letter — Letter Co')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole('link', { name: /Download Cover Letter PDF/ })).toHaveAttribute(
      'href', 'https://example.invalid/cover.pdf',
    );
  });
});
