import { ScoreBadge } from './ui/Badge';
import Button from './ui/Button';

function ScoreWithLabel({ score, label }) {
  return (
    <div className="flex flex-col items-center gap-1">
      <ScoreBadge score={score} className="text-lg" />
      <span className="text-[10px] text-stone-400 font-mono uppercase tracking-wider">{label}</span>
    </div>
  );
}

export default function TailorCard({ data, company }) {
  const link = data.drive_url || null;

  return (
    <div className="animate-fade-in border-2 border-black shadow-brutal bg-white p-5">
      <h3 className="text-xs font-bold text-stone-500 uppercase tracking-wider font-mono mb-4">
        Tailored Resume — {company}
      </h3>
      <div className="flex gap-6 items-end mb-4">
        <ScoreWithLabel score={data.ats_score} label="ATS" />
        <ScoreWithLabel score={data.hiring_manager_score} label="Hiring Mgr" />
        <ScoreWithLabel score={data.tech_recruiter_score} label="Tech Recruiter" />
      </div>
      {link ? (
        <a href={link} target="_blank" rel="noopener noreferrer">
          <Button variant="secondary" size="sm">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            Open in Google Drive
          </Button>
        </a>
      ) : (
        <p className="text-sm text-stone-400 mt-2 font-mono">Drive upload unavailable</p>
      )}
    </div>
  );
}
