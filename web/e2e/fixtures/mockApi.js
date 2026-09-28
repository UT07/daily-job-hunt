/**
 * The fake backend used by the mocked E2E project.
 *
 * WHAT THIS IS FOR, AND WHAT IT IS NOT FOR
 * ----------------------------------------
 * A mock can only ever prove things about the *frontend*. Asserting that
 * `hide_expired=true` keeps applied jobs visible against a mock proves nothing
 * -- it proves the mock. Those semantics belong to db_client.get_jobs and are
 * tested against the real thing in e2e/live/api.spec.js.
 *
 * So this file exists for two jobs, and the specs keep them separate:
 *
 *   1. RECORDING. Every request is captured (`api.requests`). Specs then assert
 *      on what the app *sent*: which params, with which defaults, on first
 *      load, after a reload, after a filter change. That is real app behaviour
 *      and it is where the "an empty dict is falsy" class of bug shows up on
 *      the client side.
 *
 *   2. CONTROLLED RESPONSES. Specs can hand the UI a response that is hard or
 *      impossible to produce live -- a 503, a 401, a `saved: false` write
 *      failure, a job that is both Applied and expired -- and assert what the
 *      UI does with it.
 *
 * The filtering below mirrors db_client.get_jobs closely enough that the UI
 * behaves realistically while a spec drives it. It is scaffolding, not an
 * oracle. Where a spec's subject is the filter semantics themselves, the spec
 * says so and runs live.
 */

import { STALE_AFTER_DAYS, ARCHIVE_AFTER_DAYS } from './jobs.js';

const ENGAGED_STATUSES = new Set([
  'Applied', 'Interviewing', 'Interviewed', 'Offer', 'Accepted', 'Rejected', 'Withdrawn',
]);

const DEFAULT_PROFILE = {
  id: '00000000-0000-4000-8000-00000000e2e2',
  email: 'playwright-e2e@naukribaba.test',
  full_name: 'E2E Test User',
  phone: '+353000000000',
  location: 'Dublin, Ireland',
  github_url: 'https://github.invalid/e2e',
  linkedin_url: 'https://linkedin.invalid/in/e2e',
  website: '',
  visa_status: 'Stamp 1G',
  work_authorizations: { IE: true },
  candidate_context: 'Platform engineer.',
  plan: 'free',
  created_at: '2026-01-01T00:00:00Z',
  gdpr_consent_at: '2026-01-01T00:00:00Z',
  salary_expectation_notes: '',
  notice_period_text: '1 month',
  onboarding_completed_at: '2026-01-02T00:00:00Z',
  profile_complete: true,
};

function ageDays(firstSeen) {
  if (!firstSeen) return null;
  const t = new Date(firstSeen).getTime();
  if (Number.isNaN(t)) return null;
  return Math.floor((Date.now() - t) / 86400000);
}

function isEngaged(job) {
  return ENGAGED_STATUSES.has(job.application_status || '');
}

export class MockApi {
  constructor({ jobs = [], profile = {} } = {}) {
    this.jobs = jobs.map((j) => ({ ...j }));
    this.profile = { ...DEFAULT_PROFILE, ...profile };
    /** Every intercepted request, in order: {method, path, search, params, body}. */
    this.requests = [];
    /** path -> {status, body} | (route info) => {status, body}. Set by specs. */
    this.overrides = new Map();
    /** Paths the app asked for that this fake has no handler for. */
    this.unhandled = [];
    this.scoreResponse = null;
    /** Overrides GET /api/pipeline/status/{name} — set to make a run FAIL. */
    this.pipelineResult = null;
    /** Set false to simulate Supabase itself being unreachable. */
    this.supabaseAuthReachable = true;
  }

  // ---- spec-facing helpers -------------------------------------------------

  /** Replace the corpus. Call before the first navigation. */
  setJobs(jobs) {
    this.jobs = jobs.map((j) => ({ ...j }));
    return this;
  }

  /** Merge fields into GET /api/profile's response. Call before navigating. */
  setProfile(patch) {
    Object.assign(this.profile, patch);
    return this;
  }

  /** Force the next/every response for a path. `handler` may be an object or fn. */
  override(pathname, handler) {
    this.overrides.set(pathname, handler);
  }

  clearOverride(pathname) {
    this.overrides.delete(pathname);
  }

  /** Requests whose pathname matches, newest last. */
  requestsFor(pathname) {
    return this.requests.filter((r) => r.path === pathname);
  }

  /** The most recent request to `pathname`, or undefined. */
  lastRequest(pathname) {
    const all = this.requestsFor(pathname);
    return all[all.length - 1];
  }

  /** GETs of the job list that actually drive the visible list (per_page > 1).
   *  Excludes the dashboard's `per_page=1` grand-total probe and the Past /
   *  Outdated shelf's `lifecycle=stale` request, which specs check separately. */
  listRequests() {
    return this.requestsFor('/api/dashboard/jobs').filter(
      (r) => r.method === 'GET' && r.params.per_page !== '1' && !r.params.lifecycle,
    );
  }

  lastListRequest() {
    const all = this.listRequests();
    return all[all.length - 1];
  }

  staleRequests() {
    return this.requestsFor('/api/dashboard/jobs').filter((r) => r.params.lifecycle === 'stale');
  }

  reset() {
    this.requests = [];
    this.unhandled = [];
  }

  // ---- the fake query ------------------------------------------------------

  /** Mirrors db_client.get_jobs. Scaffolding for the UI, not an oracle -- see
   *  the header comment. */
  queryJobs(params) {
    let rows = this.jobs.slice();
    const get = (k) => params[k];

    if (get('source')) rows = rows.filter((j) => j.source === get('source'));
    if (get('min_score') !== undefined) {
      rows = rows.filter((j) => (j.match_score ?? 0) >= Number(get('min_score')));
    }
    if (get('status')) rows = rows.filter((j) => j.application_status === get('status'));
    if (get('company')) {
      const needle = get('company').toLowerCase();
      rows = rows.filter((j) => (j.company || '').toLowerCase().includes(needle));
    }
    if (get('title')) {
      const needle = get('title').toLowerCase();
      rows = rows.filter((j) => (j.title || '').toLowerCase().includes(needle));
    }
    if (get('tailored') === 'true') rows = rows.filter((j) => !!j.resume_s3_url);
    if (get('tier')) {
      const tiers = get('tier').split(',').map((t) => t.trim());
      rows = rows.filter((j) => tiers.includes(j.score_tier));
    }
    if (get('hide_expired') === 'true') {
      // Engaged rows exempt -- the 2026-09-28 fix in db_client.get_jobs.
      rows = rows.filter((j) => !j.is_expired || isEngaged(j));
    }
    for (const key of ['archetype', 'seniority', 'remote', 'level_fit']) {
      if (get(key)) rows = rows.filter((j) => j[key] === get(key));
    }
    if (get('skill')) {
      rows = rows.filter((j) => (j.key_matches || []).includes(get('skill')));
    }

    // Age-based lifecycle. Default "not_archived" -- and deliberately applied
    // whether or not any other filter was passed, which is the shape of bug 3.
    const lifecycle = get('lifecycle') || 'not_archived';
    if (lifecycle !== 'all') {
      rows = rows.filter((j) => {
        if (isEngaged(j)) return lifecycle !== 'stale' && lifecycle !== 'archived';
        const age = ageDays(j.first_seen);
        if (age === null) return lifecycle === 'not_archived' || lifecycle === 'active';
        if (lifecycle === 'not_archived') return age < ARCHIVE_AFTER_DAYS;
        if (lifecycle === 'active') return age < STALE_AFTER_DAYS;
        if (lifecycle === 'stale') return age >= STALE_AFTER_DAYS && age < ARCHIVE_AFTER_DAYS;
        if (lifecycle === 'archived') return age >= ARCHIVE_AFTER_DAYS;
        return true;
      });
    }

    const sortBy = get('sort_by') || 'first_seen';
    const sortOrder = get('sort_order') || 'desc';
    const dir = sortOrder === 'asc' ? 1 : -1;
    rows.sort((a, b) => {
      const av = a[sortBy], bv = b[sortBy];
      if (av === bv) return 0;
      if (av === null || av === undefined) return 1;
      if (bv === null || bv === undefined) return -1;
      return (av > bv ? 1 : -1) * dir;
    });

    const page = Number(get('page') || 1);
    const perPage = Number(get('per_page') || 25);
    const total = rows.length;
    const start = (page - 1) * perPage;
    return { jobs: rows.slice(start, start + perPage), page, per_page: perPage, total };
  }

  stats() {
    // Mirrors db_client.get_job_stats: excludes expired rows.
    const live = this.jobs.filter((j) => !j.is_expired);
    const byStatus = {};
    for (const j of this.jobs) {
      const s = j.application_status || 'New';
      byStatus[s] = (byStatus[s] || 0) + 1;
    }
    const scored = live.filter((j) => typeof j.match_score === 'number');
    return {
      total_jobs: live.length,
      matched_jobs: scored.length,
      avg_match_score: scored.length
        ? scored.reduce((a, j) => a + j.match_score, 0) / scored.length
        : 0,
      jobs_by_status: byStatus,
      total_applied: byStatus.Applied || 0,
      total_interviewing: byStatus.Interviewing || 0,
      total_offers: byStatus.Offer || 0,
      total_rejected: byStatus.Rejected || 0,
    };
  }

  // ---- routing -------------------------------------------------------------

  async install(page) {
    // Supabase's own auth endpoint. VITE_SUPABASE_URL points at a host that
    // does not resolve, so without this every call to it fails at DNS.
    //
    // That matters for exactly one flow: api.js clears the local session on a
    // 401, and supabase-js's signOut POSTs to /auth/v1/logout *even for
    // scope:'local'* (GoTrueClient._signOut) -- and bails out without removing
    // the session if that POST fails with a retryable network error. Stubbing
    // it 204 models a reachable Supabase, which is the case the app is
    // designed for. `supabaseAuthReachable = false` reproduces the other one.
    await page.route('**/auth/v1/**', async (route) => {
      this.requests.push({
        method: route.request().method(),
        path: new URL(route.request().url()).pathname,
        search: '',
        params: {},
        body: null,
      });
      if (!this.supabaseAuthReachable) return route.abort('connectionfailed');
      return route.fulfill({ status: 204, contentType: 'application/json', body: '' });
    });

    await page.route('**/api/**', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const path = url.pathname;
      const params = Object.fromEntries(url.searchParams.entries());
      let body = null;
      if (request.method() !== 'GET') {
        try { body = request.postDataJSON(); } catch { body = request.postData(); }
      }
      this.requests.push({ method: request.method(), path, search: url.search, params, body });

      const override = this.overrides.get(path);
      if (override) {
        const resolved = typeof override === 'function'
          ? await override({ method: request.method(), path, params, body })
          : override;
        if (resolved) {
          return route.fulfill({
            status: resolved.status ?? 200,
            contentType: 'application/json',
            body: JSON.stringify(resolved.body ?? {}),
          });
        }
      }

      const json = (payload, status = 200) => route.fulfill({
        status,
        contentType: 'application/json',
        body: JSON.stringify(payload),
      });

      // --- dashboard ---------------------------------------------------
      if (path === '/api/dashboard/jobs' && request.method() === 'GET') {
        return json(this.queryJobs(params));
      }
      if (path === '/api/dashboard/stats') return json(this.stats());
      if (path === '/api/dashboard/skills') {
        return json({ skills: [{ name: 'Python', count: 5 }, { name: 'AWS', count: 3 }] });
      }
      if (path === '/api/dashboard/runs') return json({ runs: [] });

      const jobMatch = path.match(/^\/api\/dashboard\/jobs\/([^/]+)(\/.*)?$/);
      if (jobMatch) {
        const jobId = decodeURIComponent(jobMatch[1]);
        const sub = jobMatch[2] || '';
        const job = this.jobs.find((j) => j.job_id === jobId);
        if (sub === '/timeline' && request.method() === 'GET') return json({ events: [] });
        if (sub === '/versions') return json({ versions: [] });
        if (sub === '/sections') return json({ sections: [], jd_analysis: null });
        if (!job) return json({ detail: 'Job not found' }, 404);
        if (request.method() === 'PATCH') {
          Object.assign(job, body || {});
          return json(job);
        }
        if (request.method() === 'DELETE') {
          this.jobs = this.jobs.filter((j) => j.job_id !== jobId);
          return json({ ok: true });
        }
        if (!sub) return json(job);
        return json({ detail: `Unmocked sub-resource ${sub}` }, 501);
      }

      // --- profile / consent --------------------------------------------
      if (path === '/api/profile' && request.method() === 'GET') return json(this.profile);
      if (path === '/api/profile' && request.method() === 'PUT') {
        Object.assign(this.profile, body || {});
        return json(this.profile);
      }
      if (path === '/api/gdpr/consent') {
        this.profile.gdpr_consent_at = new Date().toISOString();
        return json({ ok: true });
      }

      // --- pipeline ------------------------------------------------------
      if (path === '/api/pipeline/status' && request.method() === 'GET') {
        return json({ last_run: null, status: 'idle', jobs_found: 0, jobs_matched: 0 });
      }
      if (path === '/api/search-config') return json({ queries: [], locations: [] });

      // Step Functions: POST returns a pollUrl, the GET below reports the
      // execution. `pipelineResult` lets a spec make it fail.
      if (path === '/api/pipeline/run-single' && request.method() === 'POST') {
        return json({ executionName: 'e2e-exec', pollUrl: '/api/pipeline/status/e2e-exec' });
      }
      if (path.startsWith('/api/pipeline/status/')) {
        return json(this.pipelineResult ?? {
          executionName: 'e2e-exec',
          status: 'SUCCEEDED',
          output: {
            job_id: 'manual-pipeline-job',
            ats_score: 91, hiring_manager_score: 89, tech_recruiter_score: 90,
            pdf_url: 'https://example.invalid/tailored.pdf',
          },
        });
      }

      // --- score: the Save & Score seam ----------------------------------
      if (path === '/api/score' && request.method() === 'POST') {
        return json(this.handleScore(body));
      }

      this.unhandled.push(`${request.method()} ${path}`);
      return json({ detail: `mockApi has no handler for ${request.method()} ${path}` }, 501);
    });
  }

  /**
   * POST /api/score. The real endpoint scores, then persists through
   * _find_or_create_job (deduped by canonical hash) and reports the outcome
   * as `saved` + `job_id`. Modelled here so a spec can walk Add Job ->
   * dashboard, and so `saved: false` is reachable via `scoreResponse`.
   */
  handleScore(body = {}) {
    if (this.scoreResponse) return this.scoreResponse;
    const score = 87;
    const company = body.company || 'Unknown';
    const title = body.job_title || 'Untitled';
    const jobId = `manual-${company}-${title}`.toLowerCase().replace(/[^a-z0-9]+/g, '-');
    const existing = this.jobs.find((j) => j.job_id === jobId);
    const row = existing || {
      job_id: jobId,
      job_hash: jobId,
      title,
      company,
      location: body.location || '',
      description: body.job_description || '',
      apply_url: body.apply_url || '',
      source: 'manual',
      application_status: 'New',
      first_seen: new Date().toISOString(),
      last_seen: new Date().toISOString(),
      is_expired: false,
      key_matches: [],
      gaps: [],
      resume_s3_url: null,
      archetype: null,
      seniority: null,
      remote: 'Unknown',
      level_fit: null,
    };
    Object.assign(row, {
      match_score: score,
      ats_score: score,
      hiring_manager_score: score,
      tech_recruiter_score: score,
      match_reasoning: 'Scored by the E2E fake backend.',
      matched_resume: body.resume_type || 'sre_devops',
      score_tier: 'A',
    });
    if (!existing) this.jobs.push(row);
    return {
      ats_score: score,
      hiring_manager_score: score,
      tech_recruiter_score: score,
      avg_score: score,
      reasoning: 'Scored by the E2E fake backend.',
      matched_resume: body.resume_type || 'sre_devops',
      job_id: jobId,
      saved: true,
    };
  }
}
