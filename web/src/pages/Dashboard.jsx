import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/useAuth';
import { apiGet } from '../api';
import { LayoutList, LayoutGrid } from 'lucide-react';
import PipelineStatus from '../components/PipelineStatus';
import StatsBar from '../components/StatsBar';
import JobTable from '../components/JobTable';
import { SkillsTags, ModelBadge, decodeHtml } from '../components/JobTable';
import { ScoreBadge } from '../components/ui/Badge';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import { Select } from '../components/ui/Input';

const SOURCES = ['All', 'adzuna', 'linkedin', 'irishjobs', 'jobs_ie', 'gradireland', 'yc', 'hn', 'web'];
const STATUS_OPTIONS = ['All', 'New', 'Applied', 'Interview', 'Offer', 'Rejected', 'Withdrawn', 'Expired'];

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
                  {decodeHtml(job.company)} {job.location && `\u00b7 ${job.location}`}
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

export default function Dashboard() {
  const { user, loading: authLoading } = useAuth();

  const [jobs, setJobs] = useState([]);
  const [stats, setStats] = useState({
    total_jobs: 0,
    matched_jobs: 0,
    avg_match_score: 0,
    jobs_by_status: {},
  });
  const [page, setPage] = useState(1);
  const [perPage] = useState(25);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Filters
  const [statusFilter, setStatusFilter] = useState('All');
  const [sourceFilter, setSourceFilter] = useState('All');
  const [minScore, setMinScore] = useState(0);
  const [companySearch, setCompanySearch] = useState('');
  const [tailoredOnly, setTailoredOnly] = useState(true);
  const [tierFilter, setTierFilter] = useState('S,A,B');
  const [hideExpired, setHideExpired] = useState(true);

  // View mode: 'list' or 'card'
  const [viewMode, setViewMode] = useState(getViewPreference);

  function toggleView(mode) {
    setViewMode(mode);
    setViewPreference(mode);
  }

  // Track filter version to avoid redundant fetches
  const [filterVersion, setFilterVersion] = useState(0);

  // Use refs for filter values so fetchJobs stays stable across filter changes
  const filtersRef = useRef({ statusFilter, sourceFilter, minScore, companySearch, tailoredOnly, tierFilter, hideExpired });
  filtersRef.current = { statusFilter, sourceFilter, minScore, companySearch, tailoredOnly, tierFilter, hideExpired };

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
      if (f.tailoredOnly) params.set('tailored', 'true');
      if (f.tierFilter !== 'All') params.set('tier', f.tierFilter);
      if (f.hideExpired) params.set('hide_expired', 'true');

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
        <Button variant="accent" size="sm">
          + Add Job
        </Button>
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

          <Select
            label="Tier"
            value={tierFilter}
            onChange={(e) => setTierFilter(e.target.value)}
            className="w-36"
          >
            <option value="S,A,B">S + A + B</option>
            <option value="S,A">S + A only</option>
            <option value="S">S only</option>
            <option value="All">All tiers</option>
            <option value="S,A,B,C">Include C</option>
          </Select>

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

          <Button variant="primary" size="md" onClick={handleFilterApply}>
            Apply Filters
          </Button>
        </div>
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

      {/* View Toggle + Job Display */}
      {!loading && (
        <div>
          <div className="flex items-center justify-between mb-3">
            <p className="text-xs font-bold text-stone-400 uppercase tracking-wider">
              {total} job{total !== 1 ? 's' : ''}
            </p>
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

          {viewMode === 'list' ? (
            <JobTable jobs={jobs} onStatusChange={handleStatusChange} onDelete={handleDelete} />
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
            onClick={() => setPage((p) => p + 1)}
            disabled={jobs.length < perPage}
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
