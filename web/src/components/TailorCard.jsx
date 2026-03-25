import ScoreBadge from './ScoreBadge';

export default function TailorCard({ data, company }) {
  const link = data.drive_url || null;

  return (
    <div className="animate-fade-in bg-slate-800 rounded-lg border border-slate-700 p-6">
      <h3 className="text-xs font-semibold text-emerald-400 uppercase tracking-wider mb-4">
        Tailored Resume — {company}
      </h3>
      <div className="flex gap-6 items-end mb-4">
        <ScoreBadge score={data.ats_score} label="ATS" />
        <ScoreBadge score={data.hiring_manager_score} label="Hiring Mgr" />
        <ScoreBadge score={data.tech_recruiter_score} label="Tech Recruiter" />
      </div>
      {link ? (
        <a
          href={link}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 mt-2 px-4 py-2 bg-emerald-600 text-white rounded-lg text-sm font-medium hover:bg-emerald-500 transition shadow-lg shadow-emerald-500/20"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          Open in Google Drive
        </a>
      ) : (
        <p className="text-sm text-slate-500 mt-2">Drive upload unavailable</p>
      )}
    </div>
  );
}
