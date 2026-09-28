/**
 * jobAge — the browser mirror of shared/job_lifecycle.py.
 *
 * The thresholds are asserted against the owner's stated rule (2026-09-28)
 * rather than against the constants, so that changing a constant without
 * meaning to breaks a test instead of silently re-cutting the dashboard.
 *
 * Ground truth for fixtures: age is measured from `first_seen`. The live
 * `jobs` table has no `created_at` column at all — a helper reaching for one
 * would return null on every row, which is what the null cases pin down.
 */
import { describe, it, expect } from 'vitest';
import {
  ageInDays,
  formatAge,
  formatJobAge,
  STALE_AFTER_DAYS,
  ARCHIVE_AFTER_DAYS,
} from '../jobAge';

const NOW = Date.parse('2026-09-28T12:00:00Z');
const daysAgo = (n) => new Date(NOW - n * 86400000).toISOString();

describe('jobAge — thresholds match the owner\'s rule', () => {
  it('stale begins at 14 days and archive at 30', () => {
    expect(STALE_AFTER_DAYS).toBe(14);
    expect(ARCHIVE_AFTER_DAYS).toBe(30);
  });
});

describe('ageInDays', () => {
  it.each([
    [0, 0],
    [1, 1],
    [13, 13],
    [14, 14],
    [27, 27],
    [30, 30],
    [186, 186],
  ])('reports %i days ago as %i', (ago, expected) => {
    expect(ageInDays(daysAgo(ago), NOW)).toBe(expected);
  });

  it('returns null for a missing timestamp rather than pretending it is new', () => {
    expect(ageInDays(null, NOW)).toBeNull();
    expect(ageInDays(undefined, NOW)).toBeNull();
    expect(ageInDays('', NOW)).toBeNull();
  });

  it('returns null for an unparseable timestamp', () => {
    expect(ageInDays('not-a-date', NOW)).toBeNull();
  });

  it('parses the Postgres-style timestamp shape the API actually returns', () => {
    // Real first_seen values come back like this from PostgREST.
    expect(ageInDays('2026-09-01T09:13:44.221291+00:00', NOW)).toBe(27);
  });

  it('floors partial days so a 27.9-day-old job never reads as 28', () => {
    expect(ageInDays(new Date(NOW - 27.9 * 86400000).toISOString(), NOW)).toBe(27);
  });
});

describe('formatAge', () => {
  it.each([
    [0, 'today'],
    [1, '1 day old'],
    [2, '2 days old'],
    [27, '27 days old'],
  ])('formats %i as "%s"', (days, expected) => {
    expect(formatAge(days)).toBe(expected);
  });

  it('renders an empty caption for an unknown age instead of "null days old"', () => {
    expect(formatAge(null)).toBe('');
    expect(formatAge(undefined)).toBe('');
  });

  it('treats a negative age (clock skew) as today, not "-1 days old"', () => {
    expect(formatAge(-3)).toBe('today');
  });
});

describe('formatJobAge', () => {
  it('composes parse + format for a real row', () => {
    expect(formatJobAge(daysAgo(27), NOW)).toBe('27 days old');
  });

  it('stays silent when first_seen is missing', () => {
    expect(formatJobAge(null, NOW)).toBe('');
  });
});
