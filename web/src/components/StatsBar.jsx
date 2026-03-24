export default function StatsBar({ stats }) {
  const cards = [
    {
      label: 'Total Jobs',
      value: stats.total_jobs ?? 0,
      color: 'text-gray-900',
      bg: 'bg-white',
    },
    {
      label: 'Applied',
      value: stats.jobs_by_status?.Applied ?? 0,
      color: 'text-blue-700',
      bg: 'bg-blue-50',
    },
    {
      label: 'Interviews',
      value: stats.jobs_by_status?.Interview ?? 0,
      color: 'text-purple-700',
      bg: 'bg-purple-50',
    },
    {
      label: 'Avg Match Score',
      value: stats.avg_match_score ?? 0,
      color:
        (stats.avg_match_score ?? 0) >= 85
          ? 'text-emerald-700'
          : (stats.avg_match_score ?? 0) >= 60
            ? 'text-amber-700'
            : 'text-red-700',
      bg:
        (stats.avg_match_score ?? 0) >= 85
          ? 'bg-emerald-50'
          : (stats.avg_match_score ?? 0) >= 60
            ? 'bg-amber-50'
            : 'bg-red-50',
    },
  ];

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
      {cards.map((c) => (
        <div
          key={c.label}
          className={`${c.bg} rounded-xl shadow-sm border border-gray-200 p-5`}
        >
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-1">
            {c.label}
          </p>
          <p className={`text-2xl font-bold ${c.color}`}>{c.value}</p>
        </div>
      ))}
    </div>
  );
}
