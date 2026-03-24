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

  const fetchJobs = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      params.set('page', page);
      params.set('per_page', perPage);
      if (statusFilter !== 'All') params.set('status', statusFilter);
      if (sourceFilter !== 'All') params.set('source', sourceFilter);
      if (minScore > 0) params.set('min_score', minScore);
      if (companySearch.trim()) params.set('company', companySearch.trim());

      const data = await apiGet(`/api/dashboard/jobs?${params.toString()}`);
      setJobs(data.jobs || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [page, perPage, statusFilter, sourceFilter, minScore, companySearch]);

  const fetchStats = useCallback(async () => {
    try {
      const data = await apiGet('/api/dashboard/stats');
      setStats(data);
    } catch (err) {
      console.error('Failed to fetch stats:', err);
    }
  }, []);

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
    // Refresh stats after a status change
    fetchStats();
  }

  function handleFilterApply() {
    setPage(1);
    // fetchJobs will re-run via useEffect when page changes
  }

  if (authLoading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="text-gray-500 text-sm">Loading...</div>
      </div>
    );
  }

  if (!user) {
    return <LoginPage />;
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-2xl">🎯</span>
            <h1 className="text-xl font-bold text-gray-900">Job Hunt</h1>
            <span className="text-sm text-gray-400 font-medium ml-2">Dashboard</span>
          </div>
          <div className="flex items-center gap-4">
            <Link
              to="/"
              className="text-sm text-gray-600 hover:text-gray-900 font-medium transition"
            >
              Tailor
            </Link>
            <span className="text-sm text-gray-500 hidden sm:block">{user.email}</span>
            <button
              onClick={signOut}
              className="text-sm text-gray-500 hover:text-gray-700 font-medium transition"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-8">
        {/* Stats */}
        <StatsBar stats={stats} />

        {/* Filter Bar */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-4 mb-6">
          <div className="flex flex-wrap items-end gap-4">
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">Status</label>
              <select
                value={statusFilter}
                onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}
                className="border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 bg-white"
              >
                {STATUS_OPTIONS.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">Source</label>
              <select
                value={sourceFilter}
                onChange={(e) => { setSourceFilter(e.target.value); setPage(1); }}
                className="border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 bg-white"
              >
                {SOURCES.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">
                Min Score: {minScore}
              </label>
              <input
                type="range"
                min={0}
                max={100}
                value={minScore}
                onChange={(e) => setMinScore(Number(e.target.value))}
                onMouseUp={() => { setPage(1); }}
                onTouchEnd={() => { setPage(1); }}
                className="w-32 accent-blue-600"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">Company</label>
              <input
                value={companySearch}
                onChange={(e) => setCompanySearch(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') { setPage(1); } }}
                placeholder="Search company..."
                className="border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 w-40"
              />
            </div>
            <button
              onClick={handleFilterApply}
              className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg text-sm font-medium transition"
            >
              Apply Filters
            </button>
          </div>
        </div>

        {/* Error */}
        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-xl p-4 mb-6">
            {error}
          </div>
        )}

        {/* Loading */}
        {loading && (
          <div className="text-center py-8">
            <div className="text-gray-500 text-sm">Loading jobs...</div>
          </div>
        )}

        {/* Job Table */}
        {!loading && <JobTable jobs={jobs} onStatusChange={handleStatusChange} />}

        {/* Pagination */}
        {!loading && jobs.length > 0 && (
          <div className="flex items-center justify-between mt-6">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              className="bg-white border border-gray-300 text-gray-700 px-4 py-2 rounded-lg text-sm font-medium
                hover:bg-gray-50 transition disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Previous
            </button>
            <span className="text-sm text-gray-500">Page {page}</span>
            <button
              onClick={() => setPage((p) => p + 1)}
              disabled={jobs.length < perPage}
              className="bg-white border border-gray-300 text-gray-700 px-4 py-2 rounded-lg text-sm font-medium
                hover:bg-gray-50 transition disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Next
            </button>
          </div>
        )}
      </main>

      {/* Footer */}
      <footer className="border-t border-gray-200 mt-12">
        <div className="max-w-7xl mx-auto px-4 py-4 text-center text-xs text-gray-400">
          Built by Utkarsh Singh -- FastAPI + React + Tailwind
        </div>
      </footer>
    </div>
  );
}
