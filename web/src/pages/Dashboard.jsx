import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../auth/useAuth';
import { apiGet } from '../api';
import StatsBar from '../components/StatsBar';
import JobTable from '../components/JobTable';
import Button from '../components/ui/Button';
import { Select } from '../components/ui/Input';

const SOURCES = ['All', 'adzuna', 'linkedin', 'irishjobs', 'jobs_ie', 'gradireland', 'yc', 'hn', 'web'];
const STATUS_OPTIONS = ['All', 'New', 'Applied', 'Interview', 'Offer', 'Rejected', 'Withdrawn'];

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

  // Track filter version to avoid redundant fetches
  const [filterVersion, setFilterVersion] = useState(0);

  const fetchJobs = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      params.set('page', String(page));
      params.set('per_page', String(perPage));
      if (statusFilter !== 'All') params.set('status', statusFilter);
      if (sourceFilter !== 'All') params.set('source', sourceFilter);
      if (minScore > 0) params.set('min_score', String(minScore));
      if (companySearch.trim()) params.set('company', companySearch.trim());
      if (tailoredOnly) params.set('tailored', 'true');

      const data = await apiGet(`/api/dashboard/jobs?${params.toString()}`);
      setJobs(data.jobs || []);
      setTotal(data.total || data.jobs?.length || 0);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [filterVersion, page, statusFilter, sourceFilter, minScore, companySearch, tailoredOnly]);

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

      {/* Pipeline status bar */}
      <div className="bg-success-light border-2 border-success px-4 py-2.5 mb-6 flex items-center gap-2">
        <span className="inline-block w-2 h-2 bg-success rounded-full animate-pulse" />
        <span className="text-sm font-medium text-success">
          Pipeline active — runs daily at 7:00 UTC
        </span>
      </div>

      {/* KPI Stats */}
      <StatsBar stats={stats} />

      {/* Filter Bar */}
      <div className="border-2 border-black bg-white p-4 mb-6">
        <div className="flex flex-wrap items-end gap-4">
          <Select
            label="Status"
            value={statusFilter}
            onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}
            className="w-36"
          >
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </Select>

          <Select
            label="Source"
            value={sourceFilter}
            onChange={(e) => { setSourceFilter(e.target.value); setPage(1); }}
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
              onChange={(e) => { setMinScore(Number(e.target.value)); setPage(1); }}
              className="w-32 accent-black"
            />
          </div>

          <div>
            <label className="block text-xs font-bold text-stone-500 uppercase tracking-wider mb-1.5">Company</label>
            <input
              value={companySearch}
              onChange={(e) => setCompanySearch(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { setPage(1); } }}
              placeholder="Search company..."
              className="bg-white border-2 border-black px-4 py-2.5 font-body text-sm text-black
                placeholder:text-stone-400 focus:outline-none focus:shadow-brutal-yellow
                transition-shadow w-40"
            />
          </div>

          <div className="flex items-center gap-2">
            <label className="block text-xs font-bold text-stone-500 uppercase tracking-wider">Tailored</label>
            <button
              onClick={() => { setTailoredOnly((v) => !v); setPage(1); }}
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

      {/* Job Table */}
      {!loading && <JobTable jobs={jobs} onStatusChange={handleStatusChange} onDelete={handleDelete} />}

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
