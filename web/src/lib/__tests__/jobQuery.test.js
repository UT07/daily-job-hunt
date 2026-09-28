/**
 * buildJobQueryParams — the single query builder shared by the active job
 * list and the Past/Outdated section.
 *
 * The point of these tests is the *difference* between the two callers: they
 * must produce byte-identical filter sets and diverge only on `lifecycle`. If
 * they drift, the active list and the past shelf silently describe different
 * corpora and the counts stop adding up.
 *
 * The "no lifecycle param" case is the one that matters most: the active list
 * relies on the backend's own `not_archived` default (db_client.get_jobs), so
 * an accidental `lifecycle=all` here would put all 1,164 archived rows back on
 * the dashboard.
 */
import { describe, it, expect } from 'vitest';
import { buildJobQueryParams } from '../jobQuery';

// Mirrors Dashboard's FILTER_DEFAULTS as they reach filtersRef.current.
function defaultFilters(overrides = {}) {
  return {
    statusFilter: 'All',
    sourceFilter: 'All',
    minScore: 60,
    companySearch: '',
    titleSearch: '',
    tailoredOnly: false,
    tierFilter: 'All',
    hideExpired: true,
    sortBy: 'first_seen',
    sortOrder: 'desc',
    archetypeFilter: 'All',
    seniorityFilter: 'All',
    remoteFilter: 'All',
    levelFitFilter: 'All',
    skillFilter: '',
    ...overrides,
  };
}

const build = (f, opts) => buildJobQueryParams(f, { page: 1, perPage: 25, ...opts }).toString();

describe('buildJobQueryParams — the active list', () => {
  it('sends NO lifecycle param, leaving the backend default (archived hidden) in charge', () => {
    const qs = build(defaultFilters());
    expect(qs).not.toContain('lifecycle');
  });

  it('carries page, per_page and the default score/expiry floor', () => {
    const params = buildJobQueryParams(defaultFilters(), { page: 3, perPage: 25 });
    expect(params.get('page')).toBe('3');
    expect(params.get('per_page')).toBe('25');
    expect(params.get('min_score')).toBe('60');
    expect(params.get('hide_expired')).toBe('true');
  });

  it('omits filters left at their neutral "All" value', () => {
    const qs = build(defaultFilters());
    for (const key of ['status', 'source', 'tier', 'archetype', 'seniority', 'remote', 'level_fit']) {
      expect(qs).not.toContain(`${key}=`);
    }
  });

  it('omits min_score entirely once the user drags it to 0', () => {
    expect(build(defaultFilters({ minScore: 0 }))).not.toContain('min_score');
  });

  it('omits hide_expired once the user turns it off', () => {
    expect(build(defaultFilters({ hideExpired: false }))).not.toContain('hide_expired');
  });

  it('trims whitespace out of the free-text filters', () => {
    const params = buildJobQueryParams(
      defaultFilters({ companySearch: '  Stripe  ', titleSearch: '  SRE  ' }),
      { page: 1, perPage: 25 }
    );
    expect(params.get('company')).toBe('Stripe');
    expect(params.get('title')).toBe('SRE');
  });

  it('drops whitespace-only free text rather than querying for spaces', () => {
    const qs = build(defaultFilters({ companySearch: '   ', titleSearch: '   ' }));
    expect(qs).not.toContain('company=');
    expect(qs).not.toContain('title=');
  });
});

describe('buildJobQueryParams — the Past/Outdated section', () => {
  it('adds lifecycle=stale', () => {
    expect(build(defaultFilters(), { lifecycle: 'stale' })).toContain('lifecycle=stale');
  });

  it('differs from the active list by lifecycle and nothing else', () => {
    const f = defaultFilters({ tierFilter: 'A', minScore: 70, companySearch: 'Acme' });
    const active = buildJobQueryParams(f, { page: 1, perPage: 25 });
    const stale = buildJobQueryParams(f, { page: 1, perPage: 25, lifecycle: 'stale' });

    stale.delete('lifecycle');
    expect(stale.toString()).toBe(active.toString());
  });

  it('inherits the tier filter so the shelf matches what the user is looking at', () => {
    expect(build(defaultFilters({ tierFilter: 'S' }), { lifecycle: 'stale' })).toContain('tier=S');
  });
});
