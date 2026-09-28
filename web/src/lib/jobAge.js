/**
 * Age-based job lifecycle — the browser-side mirror of shared/job_lifecycle.py.
 *
 * The thresholds are duplicated rather than fetched because the frontend only
 * ever *labels* age; the backend remains the single authority on which bucket
 * a row is in (it does the filtering in SQL). If these drift, the worst case is
 * a cosmetically wrong "27 days old" caption, never a job wrongly hidden.
 *
 * Age is measured from `first_seen`. There is no `created_at` column on `jobs`
 * — verified against the live table 2026-09-28 — so anything reaching for one
 * would silently render "unknown age" on every row.
 */

export const STALE_AFTER_DAYS = 14;
export const ARCHIVE_AFTER_DAYS = 30;

/**
 * Whole days since `firstSeen`, or null when it is missing/unparseable.
 *
 * Returning null (rather than 0 or Infinity) keeps the caller honest: a row
 * with a bad timestamp gets no age caption instead of a confidently wrong one.
 */
export function ageInDays(firstSeen, now = Date.now()) {
  if (!firstSeen) return null;
  const seen = new Date(firstSeen).getTime();
  if (Number.isNaN(seen)) return null;
  return Math.floor((now - seen) / 86400000);
}

/** Human caption for an age in days. Null-safe: returns '' for unknown ages. */
export function formatAge(days) {
  if (days === null || days === undefined) return '';
  if (days <= 0) return 'today';
  if (days === 1) return '1 day old';
  return `${days} days old`;
}

/** Convenience: formatAge(ageInDays(firstSeen)). */
export function formatJobAge(firstSeen, now = Date.now()) {
  return formatAge(ageInDays(firstSeen, now));
}
