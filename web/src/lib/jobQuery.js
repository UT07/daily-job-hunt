/**
 * Builds the query string for GET /api/dashboard/jobs.
 *
 * Extracted so the active job list and the Past/Outdated section send an
 * identical filter set and differ only in `lifecycle` — otherwise the two
 * lists drift and "27 jobs hidden" stops adding up against what's on screen.
 *
 * Insertion order is load-bearing: existing tests assert on the rendered query
 * string, and URLSearchParams preserves insertion order. Keep new params at
 * the end.
 */
export function buildJobQueryParams(f, { page, perPage, lifecycle } = {}) {
  const params = new URLSearchParams();
  params.set('page', String(page));
  params.set('per_page', String(perPage));
  if (f.statusFilter !== 'All') params.set('status', f.statusFilter);
  if (f.sourceFilter !== 'All') params.set('source', f.sourceFilter);
  if (f.minScore > 0) params.set('min_score', String(f.minScore));
  // One box over title AND company. Separate from `company`/`title`, which
  // are AND-ed narrowing filters — `q` is "find me this thing, wherever it
  // lives", which is what someone with 273 jobs actually wants.
  if (f.searchQuery && f.searchQuery.trim()) params.set('q', f.searchQuery.trim());
  if (f.companySearch.trim()) params.set('company', f.companySearch.trim());
  if (f.titleSearch && f.titleSearch.trim()) params.set('title', f.titleSearch.trim());
  if (f.tailoredOnly) params.set('tailored', 'true');
  if (f.tierFilter !== 'All') params.set('tier', f.tierFilter);
  if (f.hideExpired) params.set('hide_expired', 'true');
  if (f.sortBy) params.set('sort_by', f.sortBy);
  if (f.sortOrder) params.set('sort_order', f.sortOrder);
  if (f.archetypeFilter && f.archetypeFilter !== 'All') params.set('archetype', f.archetypeFilter);
  if (f.seniorityFilter && f.seniorityFilter !== 'All') params.set('seniority', f.seniorityFilter);
  if (f.remoteFilter && f.remoteFilter !== 'All') params.set('remote', f.remoteFilter);
  if (f.levelFitFilter && f.levelFitFilter !== 'All') params.set('level_fit', f.levelFitFilter);
  if (f.skillFilter && f.skillFilter.trim()) params.set('skill', f.skillFilter);
  // Omitted entirely for the active list: the backend's own default is
  // "not_archived", and spelling it out here would mean two places to change.
  if (lifecycle) params.set('lifecycle', lifecycle);
  return params;
}
