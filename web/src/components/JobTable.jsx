import { useState } from 'react';
import StatusDropdown from './StatusDropdown';

function ScoreBadge({ score }) {
  if (score == null || score === 0) return <span className="text-gray-400 text-xs">--</span>;
  const color =
    score >= 85 ? 'bg-emerald-100 text-emerald-800'
    : score >= 60 ? 'bg-amber-100 text-amber-800'
    : 'bg-red-100 text-red-800';
  return (
    <span className={`${color} text-xs font-semibold px-1.5 py-0.5 rounded-full`}>{score}</span>
  );
}

function LinkIcon({ href, label, color = 'blue' }) {
  if (!href || href === '—' || href === '--') return <span className="text-gray-300 text-xs">--</span>;
  const colors = {
    blue: 'text-blue-600 hover:text-blue-800',
    emerald: 'text-emerald-600 hover:text-emerald-800',
    purple: 'text-purple-600 hover:text-purple-800',
  };
  return (
    <a href={href} target="_blank" rel="noopener noreferrer"
       className={`${colors[color]} text-xs font-medium underline-offset-2 hover:underline`}>
      {label}
    </a>
  );
}

function ContactsCell({ contacts }) {
  const [expanded, setExpanded] = useState(false);
  if (!contacts) return <span className="text-gray-300 text-xs">--</span>;

  let parsed = [];
  try {
    parsed = typeof contacts === 'string' ? JSON.parse(contacts) : contacts;
  } catch { return <span className="text-gray-300 text-xs">--</span>; }

  if (!parsed.length) return <span className="text-gray-300 text-xs">--</span>;

  return (
    <div className="text-xs">
      <button onClick={() => setExpanded(!expanded)}
              className="text-blue-600 hover:text-blue-800 font-medium">
        {expanded ? 'Hide' : `${parsed.length} contacts`}
      </button>
      {expanded && (
        <div className="mt-1 space-y-1.5">
          {parsed.map((c, i) => (
            <div key={i} className="bg-gray-50 rounded p-2 border border-gray-100">
              <div className="font-medium text-gray-800">
                {c.name && <span>{c.name} — </span>}
                {c.role}
              </div>
              {c.why && <div className="text-gray-500 text-[10px] mt-0.5">{c.why}</div>}
              <div className="flex gap-2 mt-1">
                {c.search_url && c.search_url !== 'Find on LinkedIn' && (
                  <a href={c.search_url} target="_blank" rel="noopener noreferrer"
                     className="text-blue-500 hover:underline">LinkedIn Search</a>
                )}
                {c.google_url && (
                  <a href={c.google_url} target="_blank" rel="noopener noreferrer"
                     className="text-emerald-500 hover:underline">Google</a>
                )}
              </div>
              {c.message && (
                <div className="text-gray-500 mt-1 bg-white rounded px-1.5 py-1 border border-gray-100 line-clamp-2">
                  {c.message}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const SORTABLE_COLUMNS = [
  { key: 'first_seen', label: 'Date' },
  { key: 'match_score', label: 'Score' },
  { key: 'title', label: 'Title' },
  { key: 'company', label: 'Company' },
  { key: 'location', label: 'Location' },
  { key: 'source', label: 'Source' },
  { key: 'matched_resume', label: 'Resume Type' },
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

  function sortArrow(key) {
    if (sortKey !== key) return '';
    return sortDir === 'asc' ? ' ▲' : ' ▼';
  }

  if (jobs.length === 0) {
    return (
      <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-8 text-center">
        <p className="text-sm text-gray-500">No jobs found. Run the pipeline or adjust your filters.</p>
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
                <th key={col.key} onClick={() => handleSort(col.key)}
                    className="px-3 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider cursor-pointer hover:text-gray-700 select-none whitespace-nowrap">
                  {col.label}{sortArrow(col.key)}
                </th>
              ))}
              <th className="px-3 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider whitespace-nowrap">ATS / HM / TR</th>
              <th className="px-3 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">Assets</th>
              <th className="px-3 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">Contacts</th>
              <th className="px-3 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">Status</th>
              <th className="px-3 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">Apply</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {sorted.map((job) => (
              <tr key={job.job_id} className="hover:bg-gray-50 transition-colors">
                {/* Date */}
                <td className="px-3 py-2.5 text-gray-500 whitespace-nowrap text-xs">
                  {job.first_seen ? new Date(job.first_seen).toLocaleDateString() : '--'}
                </td>

                {/* Score */}
                <td className="px-3 py-2.5">
                  <ScoreBadge score={job.match_score} />
                </td>

                {/* Title */}
                <td className="px-3 py-2.5 font-medium text-gray-900 max-w-[200px] truncate" title={job.title}>
                  {job.title || '--'}
                </td>

                {/* Company */}
                <td className="px-3 py-2.5 text-gray-700 whitespace-nowrap">{job.company || '--'}</td>

                {/* Location */}
                <td className="px-3 py-2.5 text-gray-500 whitespace-nowrap text-xs">{job.location || '--'}</td>

                {/* Source */}
                <td className="px-3 py-2.5 text-gray-500 whitespace-nowrap text-xs">{job.source || '--'}</td>

                {/* Resume Type */}
                <td className="px-3 py-2.5 text-gray-500 whitespace-nowrap text-xs">
                  {job.matched_resume || '--'}
                </td>

                {/* ATS / HM / TR scores */}
                <td className="px-3 py-2.5 whitespace-nowrap">
                  <div className="flex items-center gap-1">
                    <ScoreBadge score={job.ats_score} />
                    <span className="text-gray-300">/</span>
                    <ScoreBadge score={job.hiring_manager_score} />
                    <span className="text-gray-300">/</span>
                    <ScoreBadge score={job.tech_recruiter_score} />
                  </div>
                </td>

                {/* Assets: Resume PDF, Cover Letter, Google Doc */}
                <td className="px-3 py-2.5">
                  <div className="flex flex-col gap-0.5">
                    <LinkIcon href={job.resume_s3_url || job.tailored_pdf_path} label="Resume" color="blue" />
                    <LinkIcon href={job.cover_letter_s3_url || job.cover_letter_pdf_path} label="Cover Letter" color="emerald" />
                    <LinkIcon href={job.resume_doc_url} label="Google Doc" color="purple" />
                  </div>
                </td>

                {/* Contacts */}
                <td className="px-3 py-2.5 max-w-[200px]">
                  <ContactsCell contacts={job.linkedin_contacts} />
                </td>

                {/* Status */}
                <td className="px-3 py-2.5">
                  <StatusDropdown
                    jobId={job.job_id}
                    currentStatus={job.application_status || 'New'}
                    onStatusChange={onStatusChange}
                  />
                </td>

                {/* Apply */}
                <td className="px-3 py-2.5">
                  {job.apply_url && job.apply_url !== 'Apply' ? (
                    <a href={job.apply_url} target="_blank" rel="noopener noreferrer"
                       className="bg-blue-600 hover:bg-blue-700 text-white text-xs font-medium px-2.5 py-1 rounded transition">
                      Apply
                    </a>
                  ) : (
                    <span className="text-gray-300 text-xs">--</span>
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
