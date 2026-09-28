/**
 * LIVE: the real FastAPI app, the real Supabase, the real SQL.
 *
 * This is the half of the suite a mock cannot do. Three of the five bugs that
 * prompted all of this lived in `db_client.get_jobs` -- an engaged-status
 * exemption missing from one filter, a lifecycle default sitting inside an
 * `if filters:` that an empty dict made falsy. Asserting those against a fake
 * backend proves only that the fake was written correctly.
 *
 * Runs only with E2E_LIVE=1, against rows owned by the synthetic user that
 * scripts/e2e_seed.py creates. Nine fixture rows, one tenant, no chance of
 * touching the owner's 1,268.
 */

import { test, expect } from '@playwright/test';

const API = process.env.E2E_API_URL || 'http://127.0.0.1:8000';
const TOKEN = process.env.E2E_JWT;
const P = 'e2e-live-';

/** GET /api/dashboard/jobs with a query object; returns the parsed payload. */
async function listJobs(request, params = {}) {
  const qs = new URLSearchParams(params).toString();
  const res = await request.get(`${API}/api/dashboard/jobs${qs ? `?${qs}` : ''}`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
  });
  expect(res.status(), await res.text()).toBe(200);
  return res.json();
}

const ids = (payload) => payload.jobs.map((j) => j.job_id).sort();

test.beforeAll(() => {
  expect(TOKEN, 'E2E_JWT is not set — run scripts/e2e_seed.py seed').toBeTruthy();
});

test.describe('tenant isolation', () => {
  test('the seeded user sees only its own rows', async ({ request }) => {
    const all = await listJobs(request, { lifecycle: 'all', per_page: '100' });
    expect(all.total).toBe(9);
    for (const job of all.jobs) expect(job.job_id.startsWith(P)).toBe(true);
  });

  test('no token is a 401', async ({ request }) => {
    const res = await request.get(`${API}/api/dashboard/jobs`);
    expect(res.status()).toBe(401);
  });

  test('a token signed with the wrong key is a 401', async ({ request }) => {
    const [h, p] = TOKEN.split('.');
    const res = await request.get(`${API}/api/dashboard/jobs`, {
      headers: { Authorization: `Bearer ${h}.${p}.not-the-real-signature` },
    });
    expect(res.status()).toBe(401);
  });
});

test.describe('bug 3 — the lifecycle default belongs OUTSIDE `if filters:`', () => {
  test('an unfiltered request still hides archived rows', async ({ request }) => {
    // The bug: `filters` is `{}` for a request with no query params, `{}` is
    // falsy, so the documented "not_archived" default never ran and all 1,251
    // rows came back instead of 87. This is the exact call shape that broke.
    const unfiltered = await listJobs(request);
    expect(ids(unfiltered)).not.toContain(`${P}archived`);
    expect(unfiltered.total).toBe(8);
  });

  test('...and so does a request that passes an unrelated filter', async ({ request }) => {
    const filtered = await listJobs(request, { min_score: '0' });
    expect(ids(filtered)).not.toContain(`${P}archived`);
  });

  test('lifecycle=all is the only way to see an archived row', async ({ request }) => {
    expect(ids(await listJobs(request, { lifecycle: 'all', per_page: '100' })))
      .toContain(`${P}archived`);
  });

  test('lifecycle=stale is exactly the 14-30 day band', async ({ request }) => {
    expect(ids(await listJobs(request, { lifecycle: 'stale' }))).toEqual([`${P}stale`]);
  });

  test('lifecycle=archived is exactly the 30+ day, unengaged rows', async ({ request }) => {
    expect(ids(await listJobs(request, { lifecycle: 'archived' }))).toEqual([`${P}archived`]);
  });

  test('lifecycle=active excludes the stale band', async ({ request }) => {
    const active = ids(await listJobs(request, { lifecycle: 'active', per_page: '100' }));
    expect(active).not.toContain(`${P}stale`);
    expect(active).not.toContain(`${P}archived`);
    // Engaged rows are exempt from every age threshold, however old.
    expect(active).toContain(`${P}applied-expired`);
    expect(active).toContain(`${P}rejected`);
  });
});

test.describe('bug 2 — hide_expired must exempt engaged rows', () => {
  test('an Applied job whose posting 404d survives hide_expired=true', async ({ request }) => {
    // Every one of the owner's 38 engaged rows carries is_expired=true,
    // because the posting 404s long after you apply. A plain
    // .eq("is_expired", False) erased every application ever sent.
    const visible = ids(await listJobs(request, { hide_expired: 'true', min_score: '0', per_page: '100' }));
    expect(visible).toContain(`${P}applied-expired`);
  });

  test('a Rejected job is engaged too, and also survives', async ({ request }) => {
    const visible = ids(await listJobs(request, { hide_expired: 'true', min_score: '0', per_page: '100' }));
    expect(visible).toContain(`${P}rejected`);
  });

  test('an expired job nobody engaged with IS hidden', async ({ request }) => {
    const visible = ids(await listJobs(request, { hide_expired: 'true', min_score: '0', per_page: '100' }));
    expect(visible).not.toContain(`${P}expired`);
  });

  test('without hide_expired, the unengaged expired job comes back', async ({ request }) => {
    expect(ids(await listJobs(request, { min_score: '0', per_page: '100' }))).toContain(`${P}expired`);
  });

  test('hide_expired only acts on the literal "true"', async ({ request }) => {
    // The backend tests `hide_expired.lower() == "true"`, and the frontend
    // omits the param rather than sending "false" -- so "false" must behave
    // as "off", not as a truthy non-empty string.
    expect(ids(await listJobs(request, { hide_expired: 'false', min_score: '0', per_page: '100' })))
      .toContain(`${P}expired`);
  });
});

test.describe('bug 5 — the tier bands', () => {
  test('each tier returns exactly the scores in its band', async ({ request }) => {
    // S 90+, A 80-89, B 70-79, C 60-69, D <60 -- the bands
    // lambdas/pipeline/score_batch.score_to_tier defines and app.py._score_tier
    // duplicates. A disagreement here is the dashboard's tier filter
    // disagreeing with the scores printed next to it.
    const bands = { S: [90, 101], A: [80, 90], B: [70, 80], C: [60, 70], D: [0, 60] };
    for (const [tier, [lo, hi]] of Object.entries(bands)) {
      const rows = (await listJobs(request, { tier, lifecycle: 'all', min_score: '0', per_page: '100' })).jobs;
      for (const job of rows) {
        expect(job.score_tier, `${job.job_id} is in the ${tier} result set`).toBe(tier);
        expect(job.match_score, `${job.job_id} scores ${job.match_score} but is tier ${tier}`)
          .toBeGreaterThanOrEqual(lo);
        expect(job.match_score).toBeLessThan(hi);
      }
    }
  });

  test('a comma-separated tier list is an OR, not a literal match', async ({ request }) => {
    const rows = ids(await listJobs(request, { tier: 'S,A', lifecycle: 'all', min_score: '0', per_page: '100' }));
    expect(rows).toContain(`${P}s-tier`);
    expect(rows).toContain(`${P}a-tier`);
    expect(rows).not.toContain(`${P}b-tier`);
  });
});

test.describe('the rest of the filters', () => {
  test('min_score is inclusive', async ({ request }) => {
    const rows = (await listJobs(request, { min_score: '84', per_page: '100' })).jobs;
    expect(rows.map((j) => j.job_id)).toContain(`${P}a-tier`); // exactly 84
    for (const job of rows) expect(job.match_score).toBeGreaterThanOrEqual(84);
  });

  test('company is a case-insensitive substring match', async ({ request }) => {
    expect(ids(await listJobs(request, { company: 'aurora' }))).toEqual([`${P}s-tier`]);
  });

  test('title is a case-insensitive substring match', async ({ request }) => {
    expect(ids(await listJobs(request, { title: 'reliability' }))).toEqual([`${P}b-tier`]);
  });

  test('source is an exact match', async ({ request }) => {
    expect(ids(await listJobs(request, { source: 'indeed' }))).toEqual([`${P}a-tier`]);
    expect((await listJobs(request, { source: 'linkedin' })).jobs.every((j) => j.source === 'linkedin')).toBe(true);
  });

  test('tailored=true returns only rows with a resume', async ({ request }) => {
    const rows = (await listJobs(request, { tailored: 'true', min_score: '0', per_page: '100' })).jobs;
    expect(rows.length).toBeGreaterThan(0);
    for (const job of rows) expect(job.resume_s3_url).toBeTruthy();
  });

  test('archetype, seniority, remote and level_fit each narrow correctly', async ({ request }) => {
    expect(ids(await listJobs(request, { archetype: 'platform_cloud' }))).toEqual([`${P}s-tier`]);
    expect(ids(await listJobs(request, { seniority: 'Staff/Lead' }))).toEqual([`${P}s-tier`]);
    expect(ids(await listJobs(request, { remote: 'Remote' }))).toEqual([`${P}s-tier`]);
    expect(ids(await listJobs(request, { level_fit: 'stretch' }))).toEqual([`${P}a-tier`]);
  });

  test('filters compose rather than replacing one another', async ({ request }) => {
    const rows = ids(await listJobs(request, { min_score: '80', source: 'linkedin', hide_expired: 'true' }));
    expect(rows).toContain(`${P}s-tier`);
    expect(rows).toContain(`${P}applied-expired`); // engaged, so hide_expired spares it
    expect(rows).not.toContain(`${P}b-tier`);      // 72, under the floor
  });
});

test.describe('sorting and pagination', () => {
  test('sort_by=match_score descending really is descending', async ({ request }) => {
    const scores = (await listJobs(request, {
      sort_by: 'match_score', sort_order: 'desc', min_score: '0', per_page: '100',
    })).jobs.map((j) => j.match_score);
    expect(scores).toEqual([...scores].sort((a, b) => b - a));
  });

  test('sort_order=asc reverses it', async ({ request }) => {
    const scores = (await listJobs(request, {
      sort_by: 'match_score', sort_order: 'asc', min_score: '0', per_page: '100',
    })).jobs.map((j) => j.match_score);
    expect(scores).toEqual([...scores].sort((a, b) => a - b));
  });

  test('an unknown sort field falls back rather than erroring', async ({ request }) => {
    const payload = await listJobs(request, { sort_by: 'definitely_not_a_column', min_score: '0' });
    expect(payload.jobs.length).toBeGreaterThan(0);
  });

  test('total is the full count, not the page size, and pages do not overlap', async ({ request }) => {
    const p1 = await listJobs(request, { per_page: '3', page: '1', min_score: '0' });
    const p2 = await listJobs(request, { per_page: '3', page: '2', min_score: '0' });
    expect(p1.total).toBe(8);
    expect(p1.jobs).toHaveLength(3);
    expect(p2.jobs).toHaveLength(3);
    expect(ids(p1).filter((id) => ids(p2).includes(id))).toEqual([]);
  });
});

test.describe('writes', () => {
  test('a status change persists and is filterable, then is put back', async ({ request }) => {
    const url = `${API}/api/dashboard/jobs/${P}b-tier`;
    const auth = { Authorization: `Bearer ${TOKEN}` };
    try {
      const patch = await request.patch(url, { headers: auth, data: { application_status: 'Interview' } });
      expect(patch.status(), await patch.text()).toBe(200);

      const filtered = await listJobs(request, { status: 'Interview', min_score: '0', per_page: '100' });
      expect(ids(filtered)).toEqual([`${P}b-tier`]);
    } finally {
      await request.patch(url, { headers: auth, data: { application_status: 'New' } });
    }
  });

  test('an invalid status is rejected with 400, not silently written', async ({ request }) => {
    const res = await request.patch(`${API}/api/dashboard/jobs/${P}b-tier`, {
      headers: { Authorization: `Bearer ${TOKEN}` },
      data: { application_status: 'Expired' },
    });
    // "Expired" is the option the dashboard's Status filter offers and the
    // backend does not accept — see specs/filter-options.spec.js.
    expect(res.status()).toBe(400);
  });

  test('a PATCH cannot touch a job belonging to someone else', async ({ request }) => {
    const res = await request.patch(`${API}/api/dashboard/jobs/definitely-not-my-job`, {
      headers: { Authorization: `Bearer ${TOKEN}` },
      data: { application_status: 'Applied' },
    });
    expect(res.status()).toBe(404);
  });
});

test.describe('stats', () => {
  test('stats exclude expired rows, which is why the dashboard needs its own probe', async ({ request }) => {
    const res = await request.get(`${API}/api/dashboard/stats`, {
      headers: { Authorization: `Bearer ${TOKEN}` },
    });
    expect(res.status()).toBe(200);
    const stats = await res.json();
    // 9 seeded rows, 3 of them expired -- and get_job_stats filters on
    // is_expired alone, with no engaged exemption. That asymmetry is exactly
    // why the dashboard needs its own unfiltered per_page=1 probe to tell
    // "no jobs yet" apart from "hidden by filters".
    expect(stats.total_jobs).toBe(6);
  });
});
