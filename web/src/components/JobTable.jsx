import { useState } from 'react';
import StatusDropdown from './StatusDropdown';

function ScoreBadge({ score }) {
  if (score == null || score === 0) return <span className="text-slate-600 text-xs font-mono">--</span>;
  const bg = score >= 85
    ? 'bg-gradient-to-r from-emerald-600 to-emerald-500 text-white'
    : score >= 60
      ? 'bg-gradient-to-r from-amber-600 to-amber-500 text-white'
      : 'bg-gradient-to-r from-red-600 to-red-500 text-white';
  return (
    <span className={`${bg} text-xs font-mono font-bold px-2 py-0.5 rounded`}>
      {score}
    </span>
  );
}

function AssetIcon({ href, icon, title }) {
  if (!href || href === '--' || href === '-') {
    return (
      <span className="text-slate-600 text-sm cursor-default" title={`No ${title}`}>
        {icon}
      </span>
    );
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title={title}
      className="text-sm hover:scale-110 transition-transform inline-block"
    >
      {icon}
    </a>
  );
}

function ContactsCell({ contacts }) {
  const [expanded, setExpanded] = useState(false);
  if (!contacts) return <span className="text-slate-600 text-xs font-mono">--</span>;

  let parsed = [];
  try {
    parsed = typeof contacts === 'string' ? JSON.parse(contacts) : contacts;
  } catch { return <span className="text-slate-600 text-xs font-mono">--</span>; }

  if (!parsed.length) return <span className="text-slate-600 text-xs font-mono">--</span>;

  return (
    <div className="text-xs">
      <button
        onClick={() => setExpanded(!expanded)}
        className="inline-flex items-center gap-1.5 text-blue-400 hover:text-blue-300 font-medium transition-colors"
      >
        <span className="bg-blue-500/10 text-blue-400 font-mono font-bold px-1.5 py-0.5 rounded text-[10px]">
          {parsed.length}
        </span>
        <span>{expanded ? 'Hide' : 'contacts'}</span>
      </button>
      {expanded && (
        <div className="mt-2 space-y-2 animate-fade-in">
          {parsed.map((c, i) => (
            <div key={i} className="bg-slate-900/80 rounded-lg p-2.5 border border-slate-700">
              <div className="font-medium text-slate-200 text-xs">
                {c.name && <span>{c.name} — </span>}
                <span className="text-slate-400">{c.role}</span>
              </div>
              {c.why && <div className="text-slate-500 text-[10px] mt-0.5">{c.why}</div>}
              <div className="flex gap-2 mt-1.5">
                {/* Real profile URL (preferred) */}
                {c.profile_url && (
                  <a href={c.profile_url} target="_blank" rel="noopener noreferrer"
                     className="inline-flex items-center gap-1 text-blue-400 hover:text-blue-300 text-[10px] font-medium bg-blue-500/10 px-2 py-0.5 rounded transition-colors">
                    <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 24 24"><path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/></svg>
                    View Profile
                  </a>
                )}
                {/* Fallback: LinkedIn search */}
                {!c.profile_url && c.search_url && c.search_url !== 'Find on LinkedIn' && (
                  <a href={c.search_url} target="_blank" rel="noopener noreferrer"
                     className="inline-flex items-center gap-1 text-slate-400 hover:text-slate-300 text-[10px] font-medium bg-slate-700/50 px-2 py-0.5 rounded transition-colors">
                    <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 24 24"><path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/></svg>
                    Search
                  </a>
                )}
                {/* Fallback: Google search */}
                {!c.profile_url && c.google_url && (
                  <a href={c.google_url} target="_blank" rel="noopener noreferrer"
                     className="inline-flex items-center gap-1 text-emerald-400 hover:text-emerald-300 text-[10px] font-medium bg-emerald-500/10 px-2 py-0.5 rounded transition-colors">
                    <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
                    Find on Google
                  </a>
                )}
              </div>
              {c.message && (
                <div className="text-slate-500 mt-1.5 bg-slate-800 rounded px-2 py-1.5 border border-slate-700 text-[10px] line-clamp-2">
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
  { key: 'tailoring_model', label: 'AI Model' },
];

function isValidUrl(str) {
  return str && (str.startsWith('http://') || str.startsWith('https://'));
}

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

  function SortArrow({ columnKey }) {
    if (sortKey !== columnKey) {
      return <span className="text-slate-600 ml-1 opacity-0 group-hover:opacity-100 transition-opacity">&#9650;</span>;
    }
    return (
      <span className="text-blue-400 ml-1">
        {sortDir === 'asc' ? '\u25B2' : '\u25BC'}
      </span>
    );
  }

  if (jobs.length === 0) {
    return (
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-12 text-center">
        <div className="text-slate-500 text-sm">
          <svg className="w-12 h-12 mx-auto mb-3 text-slate-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          No jobs found. Run the pipeline or adjust your filters.
        </div>
      </div>
    );
  }

  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm text-left">
          <thead>
            <tr className="bg-slate-700/50 border-b border-slate-700">
              {SORTABLE_COLUMNS.map((col) => (
                <th
                  key={col.key}
                  onClick={() => handleSort(col.key)}
                  className="group px-3 py-3 text-[11px] font-semibold text-slate-400 uppercase tracking-wider cursor-pointer
                    hover:text-slate-200 select-none whitespace-nowrap transition-colors"
                >
                  {col.label}
                  <SortArrow columnKey={col.key} />
                </th>
              ))}
              <th className="px-3 py-3 text-[11px] font-semibold text-slate-400 uppercase tracking-wider whitespace-nowrap">
                ATS / HM / TR
              </th>
              <th className="px-3 py-3 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Assets</th>
              <th className="px-3 py-3 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Contacts</th>
              <th className="px-3 py-3 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Status</th>
              <th className="px-3 py-3 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Apply</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700/50">
            {sorted.map((job, idx) => (
              <tr
                key={job.job_id}
                className={`hover:bg-slate-700/50 transition-colors ${
                  idx % 2 === 0 ? 'bg-slate-800' : 'bg-slate-800/50'
                }`}
              >
                {/* Date */}
                <td className="px-3 py-2.5 text-slate-400 whitespace-nowrap text-xs font-mono">
                  {job.first_seen ? new Date(job.first_seen).toLocaleDateString('en-IE', { day: '2-digit', month: 'short' }) : '--'}
                </td>

                {/* Score */}
                <td className="px-3 py-2.5">
                  <ScoreBadge score={job.match_score} />
                </td>

                {/* Title */}
                <td className="px-3 py-2.5 font-medium text-white max-w-[220px] truncate" title={job.title}>
                  {job.title || '--'}
                </td>

                {/* Company */}
                <td className="px-3 py-2.5 text-slate-300 whitespace-nowrap">{job.company || '--'}</td>

                {/* Location */}
                <td className="px-3 py-2.5 text-slate-400 whitespace-nowrap text-xs">{job.location || '--'}</td>

                {/* Source */}
                <td className="px-3 py-2.5 whitespace-nowrap">
                  <span className="bg-slate-700 text-slate-300 text-[10px] font-medium px-2 py-0.5 rounded-full">
                    {job.source || '--'}
                  </span>
                </td>

                {/* Resume Type */}
                <td className="px-3 py-2.5 text-slate-400 whitespace-nowrap text-xs">
                  {job.matched_resume || '--'}
                </td>

                {/* AI Model */}
                <td className="px-3 py-2.5 whitespace-nowrap">
                  {job.tailoring_model ? (
                    <span className="bg-violet-500/10 text-violet-400 text-[10px] font-medium px-2 py-0.5 rounded-full">
                      {job.tailoring_model}
                    </span>
                  ) : (
                    <span className="text-slate-600 text-xs font-mono">--</span>
                  )}
                </td>

                {/* ATS / HM / TR scores */}
                <td className="px-3 py-2.5 whitespace-nowrap">
                  <div className="flex items-center gap-1">
                    <ScoreBadge score={job.ats_score} />
                    <span className="text-slate-600">/</span>
                    <ScoreBadge score={job.hiring_manager_score} />
                    <span className="text-slate-600">/</span>
                    <ScoreBadge score={job.tech_recruiter_score} />
                  </div>
                </td>

                {/* Assets */}
                <td className="px-3 py-2.5">
                  <div className="flex items-center gap-2">
                    <AssetIcon
                      href={isValidUrl(job.resume_s3_url) ? job.resume_s3_url : isValidUrl(job.resume_doc_url) ? job.resume_doc_url : null}
                      icon={<span role="img" aria-label="Resume">&#128196;</span>}
                      title="Resume PDF"
                    />
                    <AssetIcon
                      href={isValidUrl(job.cover_letter_s3_url) ? job.cover_letter_s3_url : null}
                      icon={<span role="img" aria-label="Cover Letter">&#128221;</span>}
                      title="Cover Letter"
                    />
                    {isValidUrl(job.resume_doc_url) && isValidUrl(job.resume_s3_url) && (
                      <AssetIcon
                        href={job.resume_doc_url}
                        icon={<span role="img" aria-label="Google Doc">&#128203;</span>}
                        title="Google Doc"
                      />
                    )}
                  </div>
                </td>

                {/* Contacts */}
                <td className="px-3 py-2.5 max-w-[220px]">
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
                    <a
                      href={job.apply_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium px-3 py-1.5 rounded-md transition-colors
                        shadow-lg shadow-blue-600/20 hover:shadow-blue-500/30 inline-block"
                    >
                      Apply
                    </a>
                  ) : (
                    <span className="text-slate-600 text-xs font-mono">--</span>
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
