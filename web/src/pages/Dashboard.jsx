import { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../auth/useAuth';
import { apiGet } from '../api';
import LoginPage from './LoginPage';
import StatsBar from '../components/StatsBar';
import JobTable from '../components/JobTable';

const SOURCES = ['All', 'adzuna', 'linkedin', 'irishjobs', 'jobs_ie', 'gradireland', 'yc', 'hn', 'web'];
const STATUS_OPTIONS = ['All', 'New', 'Applied', 'Interview', 'Offer', 'Rejected', 'Withdrawn'];

export default function Dashboard() {
  const { user, loading: authLoading, signOut } = useAuth();

  const [jobs, setJobs] = useState([]);
  const [stats, setStats] = useState({
    total_jobs: 0,
    matched_jobs: 0,
    avg_match_score: 0,
    jobs_by_status: {},
  });
  const [page, setPage] = useState(1);
  const [perPage] = useState(25);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Filters
  const [statusFilter, setStatusFilter] = useState('All');
  const [sourceFilter, setSourceFilter] = useState('All');
  const [minScore, setMinScore] = useState(0);
  const [companySearch, setCompanySearch] = useState('');

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

      const data = await apiGet(`/api/dashboard/jobs?${params.toString()}`);
      setJobs(data.jobs || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterVersion, page]);

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

  function handleFilterApply() {
    setPage(1);
    setFilterVersion((v) => v + 1);
  }

  // Compute page numbers for pagination
  const totalPages = Math.max(1, Math.ceil((stats.total_jobs || jobs.length) / perPage));
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
      <div className="min-h-screen bg-slate-900 flex items-center justify-center">
        <div className="flex items-center gap-3">
          <span className="spinner" />
          <span className="text-slate-400 text-sm">Loading...</span>
        </div>
      </div>
    );
  }

  if (!user) {
    return <LoginPage />;
  }

  return (
    <div className="min-h-screen bg-slate-900">
      {/* Header */}
      <header className="bg-slate-800 border-b border-slate-700 sticky top-0 z-10">
        <div className="max-w-[1600px] mx-auto px-6 py-3.5 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center">
              <span className="text-white text-sm font-bold font-mono">JH</span>
            </div>
            <h1 className="text-lg font-semibold text-white">NaukriBaba</h1>
            <span className="text-xs text-slate-500 font-medium tracking-wider uppercase ml-1">Command Center</span>
          </div>
          <div className="flex items-center gap-5">
            <Link
              to="/"
              className="text-sm text-slate-400 hover:text-white font-medium transition-colors"
            >
              Tailor
            </Link>
            <span className="text-sm text-slate-500 hidden sm:block">{user.email}</span>
            <button
              onClick={signOut}
              className="text-sm text-slate-500 hover:text-slate-300 font-medium transition-colors"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-[1600px] mx-auto px-6 py-6">
        {/* Stats */}
        <StatsBar stats={stats} />

        {/* Filter Bar */}
        <div className="glass rounded-lg border border-slate-700 p-4 mb-6">
          <div className="flex flex-wrap items-end gap-4">
            <div>
              <label className="block text-[11px] font-medium text-slate-400 uppercase tracking-wider mb-1.5">Status</label>
              <select
                value={statusFilter}
                onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}
                className="bg-slate-800 border border-slate-600 text-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-colors"
              >
                {STATUS_OPTIONS.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-[11px] font-medium text-slate-400 uppercase tracking-wider mb-1.5">Source</label>
              <select
                value={sourceFilter}
                onChange={(e) => { setSourceFilter(e.target.value); setPage(1); }}
                className="bg-slate-800 border border-slate-600 text-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-colors"
              >
                {SOURCES.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-[11px] font-medium text-slate-400 uppercase tracking-wider mb-1.5">
                Min Score: <span className="font-mono text-blue-400">{minScore}</span>
              </label>
              <input
                type="range"
                min={0}
                max={100}
                value={minScore}
                onChange={(e) => setMinScore(Number(e.target.value))}
                onMouseUp={() => { setPage(1); }}
                onTouchEnd={() => { setPage(1); }}
                className="w-32 accent-blue-500"
              />
            </div>
            <div>
              <label className="block text-[11px] font-medium text-slate-400 uppercase tracking-wider mb-1.5">Company</label>
              <input
                value={companySearch}
                onChange={(e) => setCompanySearch(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') { setPage(1); } }}
                placeholder="Search company..."
                className="bg-slate-800 border border-slate-600 text-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none placeholder:text-slate-500 w-40 transition-colors"
              />
            </div>
            <button
              onClick={handleFilterApply}
              className="bg-blue-600 hover:bg-blue-500 text-white px-5 py-2 rounded-lg text-sm font-medium transition-colors shadow-lg shadow-blue-600/20"
            >
              Apply Filters
            </button>
          </div>
        </div>

        {/* Error */}
        {error && (
          <div className="bg-red-900/30 border border-red-800/50 text-red-300 text-sm rounded-lg p-4 mb-6 animate-fade-in">
            <span className="font-medium">Error:</span> {error}
          </div>
        )}

        {/* Loading */}
        {loading && (
          <div className="text-center py-12">
            <div className="flex items-center justify-center gap-3">
              <span className="spinner" />
              <span className="text-slate-400 text-sm">Loading jobs...</span>
            </div>
          </div>
        )}

        {/* Job Table */}
        {!loading && <JobTable jobs={jobs} onStatusChange={handleStatusChange} />}

        {/* Pagination */}
        {!loading && jobs.length > 0 && (
          <div className="flex items-center justify-center gap-2 mt-6">
            <button
              onClick={() => setPage(1)}
              disabled={page <= 1}
              className="bg-slate-800 border border-slate-700 text-slate-300 px-3 py-1.5 rounded-lg text-sm font-medium
                hover:bg-slate-700 hover:border-slate-600 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            >
              First
            </button>
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              className="bg-slate-800 border border-slate-700 text-slate-300 px-3 py-1.5 rounded-lg text-sm font-medium
                hover:bg-slate-700 hover:border-slate-600 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            >
              Prev
            </button>
            {getPageNumbers().map((p) => (
              <button
                key={p}
                onClick={() => setPage(p)}
                className={`px-3 py-1.5 rounded-lg text-sm font-mono font-medium transition-colors
                  ${p === page
                    ? 'bg-blue-600 text-white shadow-lg shadow-blue-600/25'
                    : 'bg-slate-800 border border-slate-700 text-slate-400 hover:bg-slate-700 hover:text-slate-200'
                  }`}
              >
                {p}
              </button>
            ))}
            <button
              onClick={() => setPage((p) => p + 1)}
              disabled={jobs.length < perPage}
              className="bg-slate-800 border border-slate-700 text-slate-300 px-3 py-1.5 rounded-lg text-sm font-medium
                hover:bg-slate-700 hover:border-slate-600 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            >
              Next
            </button>
          </div>
        )}
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-800 mt-12">
        <div className="max-w-[1600px] mx-auto px-6 py-4 text-center text-xs text-slate-600">
          Built by Utkarsh Singh -- FastAPI + React + Tailwind
        </div>
      </footer>
    </div>
  );
}
