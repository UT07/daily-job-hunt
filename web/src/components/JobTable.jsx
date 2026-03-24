import { useState } from 'react';
import StatusDropdown from './StatusDropdown';

function ScoreBadgeInline({ score }) {
  if (score == null || score === 0) return <span className="text-gray-400">--</span>;

  const color =
    score >= 85
      ? 'bg-emerald-100 text-emerald-800'
      : score >= 60
        ? 'bg-amber-100 text-amber-800'
        : 'bg-red-100 text-red-800';

  return (
    <span className={`${color} text-xs font-semibold px-2 py-0.5 rounded-full`}>
      {score}
    </span>
  );
}

const SORTABLE_COLUMNS = [
  { key: 'first_seen', label: 'Date Found' },
  { key: 'match_score', label: 'Score' },
  { key: 'title', label: 'Title' },
  { key: 'company', label: 'Company' },
  { key: 'location', label: 'Location' },
  { key: 'source', label: 'Source' },
];

export default function JobTable({ jobs, onStatusChange }) {
  const [sortKey, setSortKey] = useState('first_seen');
  const [sortDir, setSortDir] = useState('desc');

  function handleSort(key) {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  }

  const sorted = [...jobs].sort((a, b) => {
    let aVal = a[sortKey] ?? '';
    let bVal = b[sortKey] ?? '';

    if (typeof aVal === 'number' && typeof bVal === 'number') {
      return sortDir === 'asc' ? aVal - bVal : bVal - aVal;
    }

    aVal = String(aVal).toLowerCase();
    bVal = String(bVal).toLowerCase();
    if (aVal < bVal) return sortDir === 'asc' ? -1 : 1;
    if (aVal > bVal) return sortDir === 'asc' ? 1 : -1;
    return 0;
  });

  function sortIndicator(key) {
    if (sortKey !== key) return '';
    return sortDir === 'asc' ? ' \u25B2' : ' \u25BC';
  }

  if (jobs.length === 0) {
    return (
      <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-8 text-center">
        <p className="text-sm text-gray-500">
          No jobs found. Run the pipeline or adjust your filters.
        </p>
      </div>
    );
  }

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm text-left">
          <thead className="bg-gray-50 border-b border-gray-200">
            <tr>
              {SORTABLE_COLUMNS.map((col) => (
                <th
                  key={col.key}
                  onClick={() => handleSort(col.key)}
                  className="px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider cursor-pointer hover:text-gray-700 select-none whitespace-nowrap"
                >
                  {col.label}
                  {sortIndicator(col.key)}
                </th>
              ))}
              <th className="px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider whitespace-nowrap">
                ATS / HM / TR
              </th>
              <th className="px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">
                Status
              </th>
              <th className="px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">
                Apply
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {sorted.map((job) => (
              <tr key={job.job_id} className="hover:bg-gray-50 transition-colors">
                <td className="px-4 py-3 text-gray-500 whitespace-nowrap">
                  {job.first_seen
                    ? new Date(job.first_seen).toLocaleDateString()
                    : '--'}
                </td>
                <td className="px-4 py-3">
                  <ScoreBadgeInline score={job.match_score} />
                </td>
                <td className="px-4 py-3 font-medium text-gray-900 max-w-xs truncate">
                  {job.title || '--'}
                </td>
                <td className="px-4 py-3 text-gray-700 whitespace-nowrap">
                  {job.company || '--'}
                </td>
                <td className="px-4 py-3 text-gray-500 whitespace-nowrap">
                  {job.location || '--'}
                </td>
                <td className="px-4 py-3 text-gray-500 whitespace-nowrap">
                  {job.source || '--'}
                </td>
                <td className="px-4 py-3 whitespace-nowrap">
                  <span className="text-xs text-gray-500">
                    {job.ats_score ?? '--'} / {job.hiring_manager_score ?? '--'} / {job.tech_recruiter_score ?? '--'}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <StatusDropdown
                    jobId={job.job_id}
                    currentStatus={job.application_status || 'New'}
                    onStatusChange={onStatusChange}
                  />
                </td>
                <td className="px-4 py-3">
                  {job.apply_url ? (
                    <a
                      href={job.apply_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-blue-600 hover:text-blue-800 text-xs font-medium"
                    >
                      Apply
                    </a>
                  ) : (
                    <span className="text-gray-400 text-xs">--</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
