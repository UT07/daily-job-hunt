/**
 * Which artifacts a job is supposed to have, by tier.
 *
 * The owner's rule: B gets a resume only; A and S get the full set. Below B,
 * nothing is generated — tailoring 600 C-tier jobs is the "signal not volume"
 * problem, not a feature.
 *
 * Encoded here rather than inline in JSX so the dashboard, the artifacts page
 * and the tests all answer "is this job complete?" the same way. Without a
 * shared definition the answer drifts, which is how the Status filter ended up
 * offering a value the backend could never match.
 */
export const ARTIFACT_KINDS = {
  resume: { label: 'Resume', urlField: 'resume_s3_url' },
  cover_letter: { label: 'Cover Letter', urlField: 'cover_letter_s3_url' },
};

const EXPECTED_BY_TIER = {
  S: ['resume', 'cover_letter'],
  A: ['resume', 'cover_letter'],
  B: ['resume'],
  C: [],
  D: [],
};

export function expectedArtifacts(tier) {
  return EXPECTED_BY_TIER[tier] ?? [];
}

function hasArtifact(job, kind) {
  const url = job?.[ARTIFACT_KINDS[kind].urlField];
  // A stored placeholder counts as absent: an E2E run once wrote
  // "https://example.invalid/e2e-resume.pdf" into production, and a page that
  // trusts any non-empty string would offer it as a real download.
  return typeof url === 'string' && url.startsWith('http') && !url.includes('example.invalid');
}

/**
 * @returns {{tier: string, expected: string[], present: string[], missing: string[],
 *            complete: boolean, applicable: boolean}}
 */
export function artifactStatus(job) {
  const tier = job?.score_tier ?? null;
  const expected = expectedArtifacts(tier);
  const present = expected.filter((k) => hasArtifact(job, k));
  const missing = expected.filter((k) => !hasArtifact(job, k));
  return {
    tier,
    expected,
    present,
    missing,
    // A tier that expects nothing is not "complete" in any meaningful sense —
    // saying so would report 600 untailored C-tier jobs as done.
    applicable: expected.length > 0,
    complete: expected.length > 0 && missing.length === 0,
  };
}

/** Roll-up for the page header. Counts only jobs the policy applies to. */
export function summarise(jobs) {
  const applicable = (jobs || []).map(artifactStatus).filter((s) => s.applicable);
  return {
    total: applicable.length,
    complete: applicable.filter((s) => s.complete).length,
    incomplete: applicable.filter((s) => !s.complete).length,
  };
}
