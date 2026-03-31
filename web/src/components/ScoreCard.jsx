import { ScoreBadge } from './ui/Badge';

function ScoreWithLabel({ score, label }) {
  return (
    <div className="flex flex-col items-center gap-1">
      <ScoreBadge score={score} className="text-lg" />
      <span className="text-[10px] text-stone-400 font-mono uppercase tracking-wider">{label}</span>
    </div>
  );
}

export default function ScoreCard({ data, company }) {
  return (
    <div className="animate-fade-in border-2 border-black shadow-brutal bg-white p-5">
      <h3 className="text-xs font-bold text-stone-500 uppercase tracking-wider font-mono mb-4">
        Score Card — {company}
      </h3>
      <div className="flex gap-6 items-end mb-4">
        <ScoreWithLabel score={data.ats_score} label="ATS" />
        <ScoreWithLabel score={data.hiring_manager_score} label="Hiring Mgr" />
        <ScoreWithLabel score={data.tech_recruiter_score} label="Tech Recruiter" />
        <ScoreWithLabel score={data.avg_score} label="Average" />
      </div>
      <p className="text-sm text-stone-700 leading-relaxed">{data.reasoning}</p>
      <p className="text-xs text-stone-400 mt-3 font-mono">Resume: {data.matched_resume}</p>
    </div>
  );
}
