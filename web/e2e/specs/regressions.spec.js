/**
 * Regression tests for the five bugs that prompted this suite.
 *
 * Every one of them passed unit tests and was only found by touching the
 * running system. Each test below names its bug, and each is written so that
 * it FAILS against the broken behaviour -- a test that cannot fail is worse
 * than no test.
 *
 * Where the bug lived in the backend's SQL (bugs 2 and 3), the browser-side
 * test here can only cover the frontend's half of the contract; the backend
 * half is in e2e/live/api.spec.js and is marked as such on each test.
 */

import { test, expect } from '../fixtures/test.js';
import { makeJob, daysAgo, scoreToTier } from '../fixtures/jobs.js';

test.describe('bug 1 — "Save & Score" saved nothing', () => {
  test('a scored job is persisted and then shows up on the dashboard', async ({ page, dashboard, api }) => {
    await page.goto('/add-job');
    await page.getByLabel('Job Description').fill(
      'We are hiring a Senior Platform Engineer. You will own Kubernetes, '
      + 'Terraform and the AWS estate, and work with Python across the stack. '
      + 'Five years of experience with distributed systems required.',
    );
    await page.getByLabel('Job Title').fill('Senior Platform Engineer');
    await page.getByLabel('Company').fill('Manual Add Corp');
    await page.getByLabel('Location (optional)').fill('Dublin, Ireland');
    await page.getByLabel('Apply URL (optional)').fill('https://example.invalid/apply/manual');

    await page.getByRole('button', { name: 'Save & Score' }).click();
    await expect(page.getByText(/Score Card — Manual Add Corp/)).toBeVisible();

    // The POST has to carry everything the row needs -- a payload missing
    // apply_url or location saves a job the user can't apply to.
    const post = api.lastRequest('/api/score');
    expect(post.body).toMatchObject({
      job_title: 'Senior Platform Engineer',
      company: 'Manual Add Corp',
      location: 'Dublin, Ireland',
      apply_url: 'https://example.invalid/apply/manual',
      resume_type: 'sre_devops',
    });

    // And the whole point of the fix: the job is now on the dashboard.
    await dashboard.goto();
    await expect(dashboard.row('Manual Add Corp')).toHaveCount(1);
    await expect(dashboard.row('Manual Add Corp')).toContainText('Senior Platform Engineer');
  });

  /**
   * KNOWN DEFECT -- `test.fail` so the suite is green today and goes red the day
   * someone fixes it (then delete the marker).
   *
   * The backend fix (c94494d) added `saved` to ScoreResponse precisely so a
   * failed write could be told apart from a successful one: "a write that
   * fails must be distinguishable by the caller from one that worked". The UI
   * does not read it. ScoreCard.jsx renders ats/hm/tr/avg, the reasoning and
   * the resume name, and ignores both `saved` and `job_id`.
   *
   * Repro: make the jobs insert fail (RLS, a missing column, DB down) and
   * press Save & Score.
   * Expected: the card says the job was scored but not saved.
   * Observed: an ordinary green Score Card, and nothing on the dashboard --
   * the exact silent failure the backend change set out to end.
   */
  test.fail('a scored-but-not-saved result tells the user the save failed', async ({ page, api }) => {
    api.scoreResponse = {
      ats_score: 80, hiring_manager_score: 82, tech_recruiter_score: 78,
      avg_score: 80, reasoning: 'Scored fine.', matched_resume: 'sre_devops',
      job_id: null, saved: false,
    };

    await page.goto('/add-job');
    await page.getByLabel('Job Description').fill('A'.repeat(200));
    await page.getByLabel('Company').fill('Unsaved Corp');
    await page.getByRole('button', { name: 'Save & Score' }).click();

    await expect(page.getByText(/Score Card — Unsaved Corp/)).toBeVisible();
    await expect(page.getByText(/not saved|could not be saved|save failed/i))
      .toBeVisible({ timeout: 3_000 });
  });
});

test.describe('bug 2 — hide_expired erased every application ever sent', () => {
  test('an Applied job whose posting has 404d stays on the default dashboard', async ({ dashboard, api }) => {
    // All 38 engaged rows in prod carry is_expired=true, because the posting
    // 404s months after you apply. A plain is_expired=false filter wiped every
    // application the user had ever sent off the default view.
    //
    // The browser half of the contract: the app asks for hide_expired=true by
    // default and renders whatever comes back, without a second client-side
    // filter of its own. The SQL half is live/api.spec.js.
    await dashboard.goto();
    expect(api.lastListRequest().params.hide_expired).toBe('true');

    const row = dashboard.row('Echo Financial');
    await expect(row).toHaveCount(1);
    await expect(row).toContainText('Principal Engineer');
    await expect(row.getByText('EXPIRED').first()).toBeVisible();
    await expect(row.getByRole('button', { name: /Applied/i })).toBeVisible();
  });

  test('every engaged status survives the default view', async ({ dashboard, api }) => {
    const engaged = ['Applied', 'Interviewing', 'Interviewed', 'Offer', 'Accepted', 'Rejected', 'Withdrawn'];
    api.setJobs(engaged.map((status, i) => makeJob({
      job_id: `engaged-${i}`,
      title: `Engaged ${status}`,
      company: `Engaged Co ${i}`,
      application_status: status,
      is_expired: true,
      first_seen: daysAgo(200),
      match_score: 85,
    })));

    await dashboard.goto();
    await expect(dashboard.rows).toHaveCount(engaged.length);
  });
});

test.describe('bug 3 — the lifecycle default sat inside `if filters:`', () => {
  test('an unfiltered dashboard does not show a four-month-old posting', async ({ dashboard }) => {
    // An empty filter dict is falsy, so the documented "not_archived" default
    // never ran for an unfiltered request and all 1,251 rows came back instead
    // of 87. Browser half: an unengaged 140-day-old job is not on the board.
    await dashboard.goto();
    await expect(dashboard.row('Helios Legacy')).toHaveCount(0);
    // ...and not because the score filter caught it -- it scores 91.
    await dashboard.goto('?min_score=0&hide_expired=false');
    await expect(dashboard.row('Helios Legacy')).toHaveCount(0);
  });

  test('a stale (14-30 day) job is reachable in the Past / Outdated shelf', async ({ dashboard }) => {
    await dashboard.goto();
    await expect(dashboard.pastShelf).toBeVisible();
    await dashboard.pastShelf.click();
    await expect(dashboard.pastShelfRows.filter({ hasText: 'Infrastructure Engineer' })).toHaveCount(1);
  });

  /**
   * KNOWN DEFECT -- `test.fail` so the suite stays green; delete the marker when
   * it is fixed.
   *
   * The owner's rule, as written at the top of PastJobsSection.jsx: "under 14
   * days is the working dashboard, 14-30 days is past/outdated but still
   * reachable, 30+ is off the board entirely". Dashboard.jsx's own comment
   * says the same -- "the 14-30 day band lives in <PastJobsSection /> below".
   *
   * It does not. The active list sends no `lifecycle`, so the backend applies
   * its "not_archived" default, which is `age < 30` -- the stale band
   * included. Every 14-to-30-day job is therefore listed twice: once in the
   * working dashboard and again in the shelf underneath it, and the two counts
   * double-count the same rows.
   *
   * Repro: a job first_seen 20 days ago appears in both lists at once.
   * Expected: only in Past / Outdated.
   * Fix: the active list should ask for lifecycle=active, not rely on the
   * not_archived default -- one word in Dashboard.fetchJobs's
   * buildJobQueryParams call.
   */
  test.fail('a stale job is NOT also in the working list', async ({ dashboard }) => {
    await dashboard.goto();
    await expect(dashboard.row('Gamma Freight')).toHaveCount(0, { timeout: 3_000 });
  });
});

test.describe('bug 5 — two tier implementations disagreeing', () => {
  test('the tier badge on a row matches the shared S/A/B/C/D bands', async ({ dashboard, api }) => {
    // 90/80/70/60, from lambdas/pipeline/score_batch.score_to_tier -- the bands
    // app.py._score_tier was nearly written as 85/75/65/50 from memory, which
    // would have put manually scored jobs in a different tier from
    // pipeline-scored ones and made the dashboard's own tier filter disagree
    // with itself.
    const boundaries = [90, 89, 80, 79, 70, 69, 60];
    api.setJobs(boundaries.map((score, i) => makeJob({
      job_id: `band-${score}-${i}`,
      title: `Band ${score}`,
      company: `Band Co ${score}`,
      match_score: score,
      first_seen: daysAgo(1),
    })));

    await dashboard.goto('?min_score=0');
    for (const score of boundaries) {
      const row = dashboard.row(`Band Co ${score}`);
      await expect(row, `no row for score ${score}`).toHaveCount(1);
      await expect(row).toContainText(String(score));
    }

    // Selecting a tier tab must return exactly the scores in that band.
    for (const [tier, expected] of [['S', [90]], ['A', [89, 80]], ['B', [79, 70]]]) {
      await dashboard.withListResponse(() => dashboard.tierTab(tier).click());
      // Poll rather than read once: the response landing and React committing
      // the new rows are two different moments, and reading in between gives
      // you the previous tier's rows.
      await expect
        .poll(async () => (await dashboard.rowCompanies()).sort().join(','))
        .toBe(expected.map((s) => `Band Co ${s}`).sort().join(','));
      for (const score of expected) expect(scoreToTier(score)).toBe(tier);
    }
  });
});
