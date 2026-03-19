export default function ScoreBadge({ score, label, large }) {
  const color =
    score >= 85 ? 'bg-emerald-100 text-emerald-800 ring-emerald-300' :
    score >= 60 ? 'bg-amber-100 text-amber-800 ring-amber-300' :
                  'bg-red-100 text-red-800 ring-red-300';
  const size = large ? 'w-16 h-16 text-xl' : 'w-12 h-12 text-base';

  return (
    <div className="flex flex-col items-center gap-1">
      <div className={`${size} ${color} rounded-full flex items-center justify-center font-bold ring-1`}>
        {score}
      </div>
      {label && <span className="text-xs text-gray-500">{label}</span>}
    </div>
  );
}
