import ScoreBadge from './ScoreBadge';

export default function ScoreCard({ data, company }) {
  return (
    <div className="animate-fade-in bg-white rounded-xl shadow-sm border border-gray-200 p-6">
      <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-4">
        Score Card — {company}
      </h3>
      <div className="flex gap-6 items-end mb-4">
        <ScoreBadge score={data.ats_score} label="ATS" />
        <ScoreBadge score={data.hiring_manager_score} label="Hiring Mgr" />
        <ScoreBadge score={data.tech_recruiter_score} label="Tech Recruiter" />
        <ScoreBadge score={data.avg_score} label="Average" large />
      </div>
      <p className="text-sm text-gray-600 leading-relaxed">{data.reasoning}</p>
      <p className="text-xs text-gray-400 mt-3">Resume: {data.matched_resume}</p>
    </div>
  );
}
