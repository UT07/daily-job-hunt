import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../auth/useAuth';
import { apiGet } from '../api';
import { LayoutList, LayoutGrid, ArrowUpDown } from 'lucide-react';
import PipelineStatus from '../components/PipelineStatus';
import StatsBar from '../components/StatsBar';
import JobTable from '../components/JobTable';
import { SkillsTags, ModelBadge, decodeHtml } from '../components/JobTable';
import { ScoreBadge } from '../components/ui/Badge';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import { Select } from '../components/ui/Input';

const SOURCES = ['All', 'adzuna', 'linkedin', 'irishjobs', 'jobs_ie', 'gradireland', 'yc', 'hn', 'web', 'greenhouse', 'ashby', 'indeed'];
const STATUS_OPTIONS = ['All', 'New', 'Applied', 'Interview', 'Offer', 'Rejected', 'Withdrawn', 'Expired'];
const ARCHETYPES = ['All', 'sre_devops', 'backend', 'fullstack', 'platform_cloud', 'data'];
const SENIORITIES = ['All', 'Junior/Graduate', 'Mid-Level', 'Senior', 'Staff/Lead'];
const REMOTE_OPTIONS = ['All', 'Remote', 'Hybrid', 'On-site', 'Unknown'];
const LEVEL_FITS = ['All', 'exact_match', 'stretch', 'reach', 'overqualified'];

const ARCHETYPE_LABELS = {
  sre_devops: 'SRE/DevOps', backend: 'Backend', fullstack: 'Full-Stack',
  platform_cloud: 'Platform/Cloud', data: 'Data',
};
const LEVEL_FIT_COLORS = {
  exact_match: 'bg-emerald-100 text-emerald-800 border-emerald-300',
  stretch: 'bg-amber-100 text-amber-800 border-amber-300',
  reach: 'bg-red-100 text-red-800 border-red-300',
  overqualified: 'bg-sky-100 text-sky-800 border-sky-300',
};
const LEVEL_FIT_LABELS = {
  exact_match: 'Match', stretch: 'Stretch', reach: 'Reach', overqualified: 'Over',
};

function getViewPreference() {
  try { return localStorage.getItem('naukribaba_view') || 'list'; } catch { return 'list'; }
}

function setViewPreference(view) {
  try { localStorage.setItem('naukribaba_view', view); } catch { /* noop */ }
}

function CardView({ jobs, onStatusChange, onDelete }) {
  const navigate = useNavigate();

  if (jobs.length === 0) {
    return (
      <div className="border-2 border-black bg-white p-12 text-center">
        <div className="text-stone-400 text-sm">
          <svg className="w-12 h-12 mx-auto mb-3 text-stone-300" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          No jobs found. Run the pipeline or adjust your filters.
        </div>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {jobs.map((job) => {
        const isDimmed = job.application_status === 'Rejected' || job.is_expired;
        const dimClass = job.application_status === 'Rejected' ? 'opacity-40' : job.is_expired ? 'opacity-50' : '';
        const titleStrike = job.application_status === 'Rejected' ? 'line-through' : '';
        return (
        <div
          key={job.job_id}
          className={`bg-white border-2 border-black shadow-brutal cursor-pointer
            hover:translate-x-[2px] hover:translate-y-[2px] hover:shadow-brutal-sm
            transition-all ${dimClass}`}
          onClick={() => navigate(`/jobs/${job.job_id}`)}
        >
          {/* Card header */}
          <div className="p-4 border-b border-stone-200">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <p className={`font-heading font-bold text-black text-sm truncate ${titleStrike}`}>
                  {decodeHtml(job.title)}
                </p>
                <p className="text-xs text-stone-500 mt-0.5 truncate">
                  {decodeHtml(job.company)} {job.location && `· ${job.location}`}
                  {(job.posted_date || job.first_seen) && (
                    <span className="ml-1.5 text-stone-400" title={job.posted_date ? 'Date posted by company' : 'Date first seen by NaukriBaba'}>
                      · {job.posted_date ? 'Posted ' : 'Seen '}
                      {new Date(job.posted_date || job.first_seen).toLocaleDateString('en-IE', { day: 'numeric', month: 'short' })}
                    </span>
                  )}
                </p>
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                {job.score_tier && (
                  <span className={`text-[10px] font-bold px-1.5 py-0.5 border border-black ${
                    job.score_tier === 'S' ? 'bg-amber-300 text-black' :
                    job.score_tier === 'A' ? 'bg-emerald-200 text-black' :
                    job.score_tier === 'B' ? 'bg-sky-200 text-black' :
                    'bg-stone-200 text-stone-600'
                  }`}>
                    {job.score_tier}
                  </span>
                )}
                <ScoreBadge score={job.match_score} className="text-xl" />
              </div>
            </div>
          </div>

          {/* Card body */}
          <div className="p-4 space-y-3">
            {/* Skills tags */}
            <SkillsTags job={job} />

            {/* Bottom row: status, source, model */}
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 flex-wrap">
                {job.is_expired && (
                  <span className="inline-flex items-center px-2.5 py-0.5 font-mono text-[11px] font-bold uppercase tracking-wider border-2 bg-stone-200 text-stone-600 border-stone-400">
                    EXPIRED
                  </span>
                )}
                <Badge status={job.application_status || 'New'} />
                <span className="border border-stone-300 text-stone-500 font-mono text-[10px] font-bold px-1.5 py-0.5">
                  {job.source || '--'}
                </span>
                {job.archetype && (
                  <span className="border border-indigo-300 bg-indigo-50 text-indigo-700 font-mono text-[10px] font-bold px-1.5 py-0.5">
                    {ARCHETYPE_LABELS[job.archetype] || job.archetype}
                  </span>
                )}
                {job.level_fit && job.level_fit !== 'exact_match' && (
                  <span className={`font-mono text-[10px] font-bold px-1.5 py-0.5 border ${LEVEL_FIT_COLORS[job.level_fit] || 'border-stone-300 bg-stone-100'}`}>
                    {LEVEL_FIT_LABELS[job.level_fit] || job.level_fit}
                  </span>
                )}
                {job.remote && job.remote !== 'Unknown' && (
                  <span className="border border-stone-300 text-stone-500 font-mono text-[10px] font-bold px-1.5 py-0.5">
                    {job.remote}
                  </span>
                )}
                <ModelBadge model={job.tailoring_model} />
              </div>
              {job.apply_url && job.apply_url !== 'Apply' && (
                <a
                  href={job.apply_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={(e) => e.stopPropagation()}
                  className="border-2 border-black bg-black text-cream text-[10px] font-heading font-bold px-2 py-1
                    hover:bg-stone-700 transition-colors shrink-0"
                >
                  Apply
                </a>
              )}
            </div>
          </div>
        </div>
        );
      })}
    </div>
  );
}

// Filters that persist to ?key=value query params on the URL so navigating
// away and back (or sharing a link) preserves the active filter set.
// (F6 from comprehensive-prod-health plan; UC-1 option A — URL query params.)
const FILTER_DEFAULTS = {
  status: 'All',
  source: 'All',
  min_score: 60,
  company: '',
  title: '',
  tailored: false,
  tier: 'S',
  hide_expired: true,
  archetype: 'All',
  seniority: 'All',
  remote: 'All',
  level_fit: 'All',
  skill: '',
  show_advanced: false,
  sort_by: 'first_seen',
  sort_order: 'desc',
  page: 1,
};

function readFilterFromParams(params, key, fallback) {
  const raw = params.get(key);
  if (raw === null) return fallback;
  if (typeof fallback === 'boolean') return raw === 'true';
  if (typeof fallback === 'number') {
    const n = Number(raw);
    return Number.isFinite(n) ? n : fallback;
  }
  return raw;
}

export default function Dashboard() {
  const { user, loading: authLoading } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();

  const [jobs, setJobs] = useState([]);
  const [stats, setStats] = useState({
    total_jobs: 0,
    matched_jobs: 0,
    avg_match_score: 0,
    jobs_by_status: {},
  });
  const [page, setPage] = useState(() => readFilterFromParams(searchParams, 'page', FILTER_DEFAULTS.page));
  const [perPage] = useState(25);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Filters — initial values hydrated from URL query params so deep links
  // and browser-back restore the prior filter set.
  const [statusFilter, setStatusFilter] = useState(() => readFilterFromParams(searchParams, 'status', FILTER_DEFAULTS.status));
  const [sourceFilter, setSourceFilter] = useState(() => readFilterFromParams(searchParams, 'source', FILTER_DEFAULTS.source));
  const [minScore, setMinScore] = useState(() => readFilterFromParams(searchParams, 'min_score', FILTER_DEFAULTS.min_score));
  const [companySearch, setCompanySearch] = useState(() => readFilterFromParams(searchParams, 'company', FILTER_DEFAULTS.company));
  const [titleSearch, setTitleSearch] = useState(() => readFilterFromParams(searchParams, 'title', FILTER_DEFAULTS.title));
  const [tailoredOnly, setTailoredOnly] = useState(() => readFilterFromParams(searchParams, 'tailored', FILTER_DEFAULTS.tailored));
  const [tierFilter, setTierFilter] = useState(() => readFilterFromParams(searchParams, 'tier', FILTER_DEFAULTS.tier));
  const [hideExpired, setHideExpired] = useState(() => readFilterFromParams(searchParams, 'hide_expired', FILTER_DEFAULTS.hide_expired));
  const [archetypeFilter, setArchetypeFilter] = useState(() => readFilterFromParams(searchParams, 'archetype', FILTER_DEFAULTS.archetype));
  const [seniorityFilter, setSeniorityFilter] = useState(() => readFilterFromParams(searchParams, 'seniority', FILTER_DEFAULTS.seniority));
  const [remoteFilter, setRemoteFilter] = useState(() => readFilterFromParams(searchParams, 'remote', FILTER_DEFAULTS.remote));
  const [levelFitFilter, setLevelFitFilter] = useState(() => readFilterFromParams(searchParams, 'level_fit', FILTER_DEFAULTS.level_fit));
  const [skillFilter, setSkillFilter] = useState(() => readFilterFromParams(searchParams, 'skill', FILTER_DEFAULTS.skill));
  const [availableSkills, setAvailableSkills] = useState([]);
  const [showAdvanced, setShowAdvanced] = useState(() => readFilterFromParams(searchParams, 'show_advanced', FILTER_DEFAULTS.show_advanced));
  const [sortBy, setSortBy] = useState(() => readFilterFromParams(searchParams, 'sort_by', FILTER_DEFAULTS.sort_by));
  const [sortOrder, setSortOrder] = useState(() => readFilterFromParams(searchParams, 'sort_order', FILTER_DEFAULTS.sort_order));

  // View mode: 'list' or 'card'
  const [viewMode, setViewMode] = useState(getViewPreference);

  function toggleView(mode) {
    setViewMode(mode);
    setViewPreference(mode);
  }

  // Track filter version to avoid redundant fetches
  const [filterVersion, setFilterVersion] = useState(0);

  // Use refs for filter values so fetchJobs stays stable across filter changes
  const filtersRef = useRef({ statusFilter, sourceFilter, minScore, companySearch, titleSearch, tailoredOnly, tierFilter, hideExpired, sortBy, sortOrder, archetypeFilter, seniorityFilter, remoteFilter, levelFitFilter, skillFilter });
  filtersRef.current = { statusFilter, sourceFilter, minScore, companySearch, titleSearch, tailoredOnly, tierFilter, hideExpired, sortBy, sortOrder, archetypeFilter, seniorityFilter, remoteFilter, levelFitFilter, skillFilter };

  // Sync the URL query string to current filter state. Only writes the keys
  // whose values differ from the defaults so the URL stays clean for the
  // common case (one-click links from elsewhere in the app).
  useEffect(() => {
    const next = new URLSearchParams();
    const currentValues = {
      status: statusFilter,
      source: sourceFilter,
      min_score: minScore,
      company: companySearch,
      title: titleSearch,
      tailored: tailoredOnly,
      tier: tierFilter,
      hide_expired: hideExpired,
      archetype: archetypeFilter,
      seniority: seniorityFilter,
      remote: remoteFilter,
      level_fit: levelFitFilter,
      skill: skillFilter,
      show_advanced: showAdvanced,
      sort_by: sortBy,
      sort_order: sortOrder,
      page,
    };
    for (const [key, value] of Object.entries(currentValues)) {
      const fallback = FILTER_DEFAULTS[key];
      if (value === fallback || value === '' || value === null || value === undefined) continue;
      next.set(key, String(value));
    }
    // Use replace to avoid spamming history with every keystroke.
    setSearchParams(next, { replace: true });
    // Intentionally exclude setSearchParams to keep the effect single-source-of-truth.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter, sourceFilter, minScore, companySearch, titleSearch, tailoredOnly, tierFilter, hideExpired, archetypeFilter, seniorityFilter, remoteFilter, levelFitFilter, skillFilter, showAdvanced, sortBy, sortOrder, page]);

  const fetchJobs = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const f = filtersRef.current;
      const params = new URLSearchParams();
      params.set('page', String(page));
      params.set('per_page', String(perPage));
      if (f.statusFilter !== 'All') params.set('status', f.statusFilter);
      if (f.sourceFilter !== 'All') params.set('source', f.sourceFilter);
      if (f.minScore > 0) params.set('min_score', String(f.minScore));
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

      const data = await apiGet(`/api/dashboard/jobs?${params.toString()}`);
      setJobs(data.jobs || []);
      setTotal(data.total || data.jobs?.length || 0);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [filterVersion, page, perPage]);

  const fetchStats = useCallback(async () => {
    try {
      const data = await apiGet('/api/dashboard/stats');
      setStats(data);
    } catch (err) {
      console.error('Failed to fetch stats:', err);
    }
  }, []);

  // Fetch available skills for filter dropdown.
  // Was silently swallowed (.catch(() => {})) — if the endpoint failed the
  // skills filter just stayed empty with no clue why. Downgrade to a warn
  // so devs at least see it in the console; it's a filter convenience, so
  // we don't gate the rest of the dashboard on it.
  useEffect(() => {
    if (user) {
      apiGet('/api/dashboard/skills').then((data) => {
        if (data?.skills) setAvailableSkills(data.skills);
      }).catch((err) => {
        console.warn('Failed to load skills filter:', err.message);
      });
    }
  }, [user]);

  // Fetch only on mount and explicit filter apply (not on every keystroke)
  useEffect(() => {
    if (user) {
      fetchJobs();
      fetchStats();
    }
  }, [user, fetchJobs, fetchStats]);

  function handleStatusChange(jobId, newStatus) {
    setJobs((prev) =>
      prev.map((j) =>
        j.job_id === jobId ? { ...j, application_status: newStatus } : j
      )
    );
    fetchStats();
  }

  function handleDelete(jobId) {
    setJobs((prev) => prev.filter((j) => j.job_id !== jobId));
    setTotal((t) => Math.max(0, t - 1));
    fetchStats();
  }

  function handleFilterApply() {
    setPage(1);
    setFilterVersion((v) => v + 1);
  }

  // Compute page numbers for pagination
  const totalPages = Math.max(1, Math.ceil((total || jobs.length) / perPage));
  function getPageNumbers() {
    const pages = [];
    const maxVisible = 5;
    let start = Math.max(1, page - Math.floor(maxVisible / 2));
    let end = Math.min(totalPages, start + maxVisible - 1);
    if (end - start < maxVisible - 1) {
      start = Math.max(1, end - maxVisible + 1);
    }
    for (let i = start; i <= end; i++) pages.push(i);
    return pages;
  }

  if (authLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <span className="spinner" />
      </div>
    );
  }

  return (
    <div>
      {/* Page header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-heading font-bold text-black tracking-tight">
            Job Dashboard
          </h1>
          <p className="text-sm text-stone-500 mt-0.5">
            Your AI-powered job search command center
          </p>
        </div>
        <a href="/add-job">
          <Button variant="accent" size="sm">
            + Add Job
          </Button>
        </a>
      </div>

      {/* Pipeline status + Run button */}
      <PipelineStatus onComplete={() => { fetchJobs(); fetchStats(); }} />

      {/* KPI Stats */}
      <StatsBar stats={stats} />

      {/* Filter Bar */}
      <div className="border-2 border-black bg-white p-4 mb-6">
        <div className="flex flex-wrap items-end gap-4">
          <Select
            label="Status"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="w-36"
          >
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </Select>

          <Select
            label="Source"
            value={sourceFilter}
            onChange={(e) => setSourceFilter(e.target.value)}
            className="w-36"
          >
            {SOURCES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </Select>

          <div>
            <label className="block text-xs font-bold text-stone-500 uppercase tracking-wider mb-1.5">
              Min Score: <span className="font-mono text-black">{minScore}</span>
            </label>
            <input
              type="range"
              min={0}
              max={100}
              value={minScore}
              onChange={(e) => setMinScore(Number(e.target.value))}
              className="w-32 accent-black"
            />
          </div>

          <div>
            <label className="block text-xs font-bold text-stone-500 uppercase tracking-wider mb-1.5">Title</label>
            <input
              value={titleSearch}
              onChange={(e) => setTitleSearch(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleFilterApply(); }}
              placeholder="Search title..."
              className="bg-white border-2 border-black px-4 py-2.5 font-body text-sm text-black
                placeholder:text-stone-400 focus:outline-none focus:shadow-brutal-yellow
                transition-shadow w-40"
            />
          </div>

          <div>
            <label className="block text-xs font-bold text-stone-500 uppercase tracking-wider mb-1.5">Company</label>
            <input
              value={companySearch}
              onChange={(e) => setCompanySearch(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleFilterApply(); }}
              placeholder="Search company..."
              className="bg-white border-2 border-black px-4 py-2.5 font-body text-sm text-black
                placeholder:text-stone-400 focus:outline-none focus:shadow-brutal-yellow
                transition-shadow w-40"
            />
          </div>

          <div className="flex items-center gap-2">
            <label className="block text-xs font-bold text-stone-500 uppercase tracking-wider">Tailored</label>
            <button
              onClick={() => setTailoredOnly((v) => !v)}
              className={`relative inline-flex h-6 w-11 items-center border-2 border-black transition-colors cursor-pointer ${
                tailoredOnly ? 'bg-black' : 'bg-white'
              }`}
            >
              <span
                className={`inline-block h-4 w-4 transform transition-transform ${
                  tailoredOnly ? 'translate-x-[22px] bg-yellow' : 'translate-x-[2px] bg-stone-300'
                }`}
              />
            </button>
          </div>

          <div className="flex items-center gap-2">
            <label className="block text-xs font-bold text-stone-500 uppercase tracking-wider">Hide Expired</label>
            <button
              onClick={() => setHideExpired((v) => !v)}
              className={`relative inline-flex h-6 w-11 items-center border-2 border-black transition-colors cursor-pointer ${
                hideExpired ? 'bg-black' : 'bg-white'
              }`}
            >
              <span
                className={`inline-block h-4 w-4 transform transition-transform ${
                  hideExpired ? 'translate-x-[22px] bg-yellow' : 'translate-x-[2px] bg-stone-300'
                }`}
              />
            </button>
          </div>

          <button
            onClick={() => setShowAdvanced((v) => !v)}
            className="text-xs font-bold text-stone-500 uppercase tracking-wider hover:text-black transition-colors cursor-pointer underline"
          >
            {showAdvanced ? '− Less' : '+ Advanced'}
          </button>

          <Button variant="primary" size="md" onClick={handleFilterApply}>
            Apply Filters
          </Button>
        </div>

        {/* Advanced Filters */}
        {showAdvanced && (
          <div className="flex flex-wrap items-end gap-4 mt-4 pt-4 border-t border-stone-200">
            <Select
              label="Archetype"
              value={archetypeFilter}
              onChange={(e) => { setArchetypeFilter(e.target.value); handleFilterApply(); }}
              className="w-36"
            >
              {ARCHETYPES.map((a) => (
                <option key={a} value={a}>{a === 'All' ? 'All' : ARCHETYPE_LABELS[a] || a}</option>
              ))}
            </Select>

            <Select
              label="Seniority"
              value={seniorityFilter}
              onChange={(e) => { setSeniorityFilter(e.target.value); handleFilterApply(); }}
              className="w-36"
            >
              {SENIORITIES.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </Select>

            <Select
              label="Remote"
              value={remoteFilter}
              onChange={(e) => { setRemoteFilter(e.target.value); handleFilterApply(); }}
              className="w-32"
            >
              {REMOTE_OPTIONS.map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </Select>

            <Select
              label="Level Fit"
              value={levelFitFilter}
              onChange={(e) => { setLevelFitFilter(e.target.value); handleFilterApply(); }}
              className="w-36"
            >
              {LEVEL_FITS.map((l) => (
                <option key={l} value={l}>{l === 'All' ? 'All' : LEVEL_FIT_LABELS[l] || l}</option>
              ))}
            </Select>

            <div className="flex flex-col gap-1">
              <label className="text-[10px] font-bold text-stone-500 uppercase tracking-wider">Skill</label>
              <div className="relative">
                <input
                  list="skill-options"
                  value={skillFilter}
                  onChange={(e) => {
                    setSkillFilter(e.target.value);
                    if (!e.target.value || availableSkills.some((s) => s.name === e.target.value)) {
                      handleFilterApply();
                    }
                  }}
                  placeholder="Type to search..."
                  className="bg-white border-2 border-black px-2 py-1 text-xs font-heading font-bold w-44
                    focus:outline-none focus:shadow-brutal-yellow placeholder:text-stone-400 placeholder:font-normal"
                />
                {skillFilter && (
                  <button
                    onClick={() => { setSkillFilter(''); handleFilterApply(); }}
                    className="absolute right-1 top-1/2 -translate-y-1/2 text-stone-400 hover:text-black text-sm"
                  >
                    ✕
                  </button>
                )}
                <datalist id="skill-options">
                  {availableSkills.map((s) => (
                    <option key={s.name} value={s.name}>{s.name} ({s.count})</option>
                  ))}
                </datalist>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Error */}
      {error && (
        <div className="bg-error-light border-2 border-error text-error text-sm p-4 mb-6 animate-fade-in">
          <span className="font-bold">Error:</span> {error}
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="text-center py-12">
          <div className="flex items-center justify-center gap-3">
            <span className="spinner" />
            <span className="text-stone-500 text-sm">Loading jobs...</span>
          </div>
        </div>
      )}

      {/* Tier Tabs */}
      {!loading && (
        <div className="flex items-center gap-0 mb-6 border-b-2 border-black">
          {[
            { key: 'S', label: 'Must Apply', color: 'bg-amber-300 text-black border-amber-400' },
            { key: 'A', label: 'Strong Match', color: 'bg-emerald-100 text-black border-emerald-300' },
            { key: 'B', label: 'Worth Trying', color: 'bg-sky-100 text-black border-sky-300' },
            { key: 'All', label: 'All Jobs', color: 'bg-stone-100 text-stone-600 border-stone-300' },
          ].map((tier) => {
            const isActive = tierFilter === (tier.key === 'All' ? 'All' : tier.key);
            const count = tier.key === 'All' ? total : jobs.filter(j => j.score_tier === tier.key).length;
            return (
              <button
                key={tier.key}
                onClick={() => {
                  setTierFilter(tier.key === 'All' ? 'All' : tier.key);
                  setFilterVersion(v => v + 1);
                }}
                className={`px-5 py-2.5 text-sm font-heading font-bold transition-all border-b-3 -mb-[2px] cursor-pointer ${
                  isActive
                    ? `${tier.color} border-black`
                    : 'bg-white text-stone-400 border-transparent hover:text-black hover:bg-stone-50'
                }`}
              >
                <span className={`inline-block w-5 h-5 text-[10px] font-bold leading-5 text-center border mr-1.5 ${
                  tier.key === 'S' ? 'bg-amber-300 border-amber-500' :
                  tier.key === 'A' ? 'bg-emerald-200 border-emerald-400' :
                  tier.key === 'B' ? 'bg-sky-200 border-sky-400' :
                  'bg-stone-200 border-stone-400'
                }`}>{tier.key === 'All' ? '∞' : tier.key}</span>
                {tier.label}
                {isActive && <span className="ml-1.5 font-mono text-xs">({total})</span>}
              </button>
            );
          })}
        </div>
      )}

      {/* View Toggle + Job Display */}
      {!loading && (
        <div>
          <div className="flex items-center justify-between mb-3">
            <p className="text-xs font-bold text-stone-400 uppercase tracking-wider">
              {total} job{total !== 1 ? 's' : ''}{tierFilter !== 'All' && ` in Tier ${tierFilter}`}
            </p>
            <div className="flex items-center gap-3">
              {/* Sort control */}
              <div className="flex items-center gap-1.5">
                <ArrowUpDown size={14} className="text-stone-400" />
                <select
                  value={`${sortBy}:${sortOrder}`}
                  onChange={(e) => {
                    const [field, order] = e.target.value.split(':');
                    setSortBy(field);
                    setSortOrder(order);
                    setPage(1);
                    setFilterVersion((v) => v + 1);
                  }}
                  className="bg-white border-2 border-black px-2 py-1 text-xs font-heading font-bold
                    focus:outline-none focus:shadow-brutal-yellow cursor-pointer"
                >
                  <option value="match_score:desc">Score (highest)</option>
                  <option value="match_score:asc">Score (lowest)</option>
                  <option value="first_seen:desc">Seen (newest)</option>
                  <option value="first_seen:asc">Seen (oldest)</option>
                  <option value="posted_date:desc">Posted (newest)</option>
                  <option value="posted_date:asc">Posted (oldest)</option>
                  <option value="company:asc">Company (A-Z)</option>
                  <option value="company:desc">Company (Z-A)</option>
                  <option value="title:asc">Title (A-Z)</option>
                  <option value="title:desc">Title (Z-A)</option>
                </select>
              </div>
            <div className="flex items-center border-2 border-black">
              <button
                onClick={() => toggleView('list')}
                className={`p-1.5 transition-colors cursor-pointer ${
                  viewMode === 'list'
                    ? 'bg-black text-cream'
                    : 'bg-white text-stone-400 hover:text-black'
                }`}
                title="List view"
              >
                <LayoutList size={16} />
              </button>
              <button
                onClick={() => toggleView('card')}
                className={`p-1.5 transition-colors cursor-pointer border-l-2 border-black ${
                  viewMode === 'card'
                    ? 'bg-black text-cream'
                    : 'bg-white text-stone-400 hover:text-black'
                }`}
                title="Card view"
              >
                <LayoutGrid size={16} />
              </button>
            </div>
            </div>
          </div>

          {viewMode === 'list' ? (
            <JobTable
              jobs={jobs}
              onStatusChange={handleStatusChange}
              onDelete={handleDelete}
              sortBy={sortBy}
              sortOrder={sortOrder}
              onSortChange={(field, order) => {
                setSortBy(field);
                setSortOrder(order);
                setFilterVersion((v) => v + 1);
              }}
            />
          ) : (
            <CardView jobs={jobs} onStatusChange={handleStatusChange} onDelete={handleDelete} />
          )}
        </div>
      )}

      {/* Pagination */}
      {!loading && jobs.length > 0 && (
        <div className="flex items-center justify-center gap-2 mt-6">
          <button
            onClick={() => setPage(1)}
            disabled={page <= 1}
            className="border-2 border-black bg-white text-black px-3 py-1.5 text-sm font-heading font-bold
              hover:bg-stone-100 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
          >
            First
          </button>
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
            className="border-2 border-black bg-white text-black px-3 py-1.5 text-sm font-heading font-bold
              hover:bg-stone-100 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
          >
            Prev
          </button>
          {getPageNumbers().map((p) => (
            <button
              key={p}
              onClick={() => setPage(p)}
              className={`border-2 border-black px-3 py-1.5 text-sm font-mono font-bold transition-colors
                ${p === page
                  ? 'bg-yellow text-black'
                  : 'bg-white text-stone-600 hover:bg-stone-100'
                }`}
            >
              {p}
            </button>
          ))}
          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page >= totalPages || jobs.length < perPage}
            className="border-2 border-black bg-white text-black px-3 py-1.5 text-sm font-heading font-bold
              hover:bg-stone-100 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
