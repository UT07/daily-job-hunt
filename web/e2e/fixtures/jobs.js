/**
 * Deterministic job fixtures.
 *
 * Column names are the real ones -- `first_seen` (there is no `created_at` on
 * `jobs`), `score_tier`, `match_score`, `application_status`, `is_expired`,
 * `job_hash`, `resume_s3_url`. A fixture that invents a column would make the
 * suite pass against a shape the app never sees.
 *
 * Ages are expressed in days-before-now so the lifecycle bands (active < 14,
 * stale 14-30, archived >= 30) land where the test means them to, whatever day
 * the suite runs.
 */

export const STALE_AFTER_DAYS = 14;
export const ARCHIVE_AFTER_DAYS = 30;

export function daysAgo(days) {
  return new Date(Date.now() - days * 86400000).toISOString();
}

let counter = 0;

/** Tier bands, duplicated from lambdas/pipeline/score_batch.score_to_tier
 *  (S 90+, A 80-89, B 70-79, C 60-69, D <60) so fixtures can't invent a
 *  disagreeing mapping -- the exact drift that commit c94494d warns about. */
export function scoreToTier(score) {
  if (score === null || score === undefined) return 'D';
  if (score >= 90) return 'S';
  if (score >= 80) return 'A';
  if (score >= 70) return 'B';
  if (score >= 60) return 'C';
  return 'D';
}

export function makeJob(overrides = {}) {
  counter += 1;
  const score = overrides.match_score ?? 75;
  const base = {
    job_id: `e2e-job-${String(counter).padStart(3, '0')}`,
    job_hash: `e2e-hash-${String(counter).padStart(3, '0')}`,
    user_id: '00000000-0000-4000-8000-00000000e2e2',
    title: `Test Engineer ${counter}`,
    company: `Company ${counter}`,
    location: 'Dublin, Ireland',
    description: 'We need someone who knows Python, AWS and Kubernetes.',
    apply_url: `https://example.invalid/jobs/${counter}`,
    source: 'linkedin',
    match_score: score,
    ats_score: score,
    hiring_manager_score: score,
    tech_recruiter_score: score,
    score_tier: scoreToTier(score),
    matched_resume: 'sre_devops',
    application_status: 'New',
    first_seen: daysAgo(2),
    last_seen: daysAgo(1),
    is_expired: false,
    key_matches: ['Python', 'AWS'],
    gaps: [],
    match_reasoning: 'Strong overlap on platform work.',
    resume_s3_url: null,
    cover_letter_s3_url: null,
    tailoring_model: null,
    archetype: 'backend',
    seniority: 'Mid-Level',
    remote: 'Hybrid',
    level_fit: 'exact_match',
  };
  const job = { ...base, ...overrides };
  // Keep tier consistent with score unless a test deliberately overrides it.
  if (overrides.match_score !== undefined && overrides.score_tier === undefined) {
    job.score_tier = scoreToTier(overrides.match_score);
  }
  return job;
}

export function resetJobCounter() {
  counter = 0;
}

/**
 * The standard corpus. Small, but every row exists to make one specific
 * assertion possible, so nothing here is filler.
 */
export function standardJobs() {
  resetJobCounter();
  return [
    // -- the headline row: top tier, recent, untouched
    makeJob({
      job_id: 'job-s-tier', job_hash: 'hash-s-tier',
      title: 'Staff Platform Engineer', company: 'Aurora Systems',
      match_score: 93, source: 'linkedin', first_seen: daysAgo(1),
      archetype: 'platform_cloud', seniority: 'Staff/Lead', remote: 'Remote',
      resume_s3_url: 'https://example.invalid/resume-s.pdf',
      tailoring_model: 'llama-3.3-70b',
    }),
    makeJob({
      job_id: 'job-a-tier', job_hash: 'hash-a-tier',
      title: 'Senior Backend Engineer', company: 'Borealis Labs',
      match_score: 84, source: 'indeed', first_seen: daysAgo(3),
      archetype: 'backend', seniority: 'Senior', remote: 'Hybrid',
      level_fit: 'stretch',
    }),
    makeJob({
      job_id: 'job-b-tier', job_hash: 'hash-b-tier',
      title: 'Site Reliability Engineer', company: 'Cygnus Cloud',
      match_score: 72, source: 'adzuna', first_seen: daysAgo(5),
      archetype: 'sre_devops', seniority: 'Mid-Level', remote: 'On-site',
    }),
    // -- below the default min_score of 60: must be invisible by default
    makeJob({
      job_id: 'job-low-score', job_hash: 'hash-low-score',
      title: 'Junior Support Analyst', company: 'Delta Retail',
      match_score: 41, source: 'hn_hiring', first_seen: daysAgo(4),
      seniority: 'Junior/Graduate',
    }),
    // -- BUG 2's row. Applied months ago, posting has since 404'd. Must stay
    //    visible with hide_expired on AND survive the archive age gate.
    makeJob({
      job_id: 'job-applied-expired', job_hash: 'hash-applied-expired',
      title: 'Principal Engineer', company: 'Echo Financial',
      match_score: 88, source: 'linkedin',
      application_status: 'Applied', is_expired: true,
      first_seen: daysAgo(120),
      resume_s3_url: 'https://example.invalid/resume-applied.pdf',
    }),
    // -- plain expired, never engaged: hidden by default, shown when the
    //    Hide Expired toggle is turned off
    makeJob({
      job_id: 'job-expired', job_hash: 'hash-expired',
      title: 'Cloud Engineer', company: 'Foxtrot Media',
      match_score: 76, is_expired: true, source: 'glassdoor', first_seen: daysAgo(6),
    }),
    // -- stale band (14-30 days): belongs in Past / Outdated, not the main list
    makeJob({
      job_id: 'job-stale', job_hash: 'hash-stale',
      title: 'Infrastructure Engineer', company: 'Gamma Freight',
      match_score: 81, first_seen: daysAgo(20), source: 'irishjobs',
    }),
    // -- archived (>= 30 days), never engaged: must not appear anywhere by default
    makeJob({
      job_id: 'job-archived', job_hash: 'hash-archived',
      title: 'Ancient Posting', company: 'Helios Legacy',
      match_score: 91, first_seen: daysAgo(140), source: 'jobs_ie',
    }),
    // -- rejected: engaged, so age-exempt; rendered struck through and dimmed
    makeJob({
      job_id: 'job-rejected', job_hash: 'hash-rejected',
      title: 'Data Platform Engineer', company: 'Iris Analytics',
      match_score: 79, application_status: 'Rejected',
      first_seen: daysAgo(60), source: 'greenhouse', archetype: 'data',
    }),
  ];
}

/**
 * A page's worth and then some, for pagination tests (per_page is 25).
 *
 * Every row is deliberately >= the default min_score of 60 and < 14 days old,
 * so the default filter set does not quietly shrink the corpus out from under
 * a pagination assertion. `first_seen` steps by 0.1 days so the default
 * first_seen:desc sort gives one unambiguous order: Bulk Role 000 first.
 */
export function manyJobs(count = 60) {
  resetJobCounter();
  return Array.from({ length: count }, (_, i) =>
    makeJob({
      job_id: `bulk-${String(i).padStart(3, '0')}`,
      job_hash: `bulk-hash-${String(i).padStart(3, '0')}`,
      title: `Bulk Role ${String(i).padStart(3, '0')}`,
      company: `Bulk Co ${String(i).padStart(3, '0')}`,
      match_score: 95 - (i % 30),
      first_seen: daysAgo(1 + i * 0.1),
    }),
  );
}
