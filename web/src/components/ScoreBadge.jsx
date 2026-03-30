export default function ScoreBadge({ score, label, large }) {
  if (score == null || score === 0) return <span className="text-gray-500 text-sm">--</span>
  const color =
    score >= 85 ? 'bg-emerald-900/50 text-emerald-300 ring-emerald-500/50' :
    score >= 60 ? 'bg-amber-900/50 text-amber-300 ring-amber-500/50' :
                  'bg-red-900/50 text-red-300 ring-red-500/50';
  const size = large ? 'w-16 h-16 text-xl' : 'w-12 h-12 text-base';

  return (
    <div className="flex flex-col items-center gap-1">
      <div className={`${size} ${color} rounded-full flex items-center justify-center font-bold ring-1 font-mono`}>
        {score}
      </div>
      {label && <span className="text-xs text-slate-400">{label}</span>}
    </div>
  );
}
