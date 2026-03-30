export default function StatsBar({ stats }) {
  const cards = [
    {
      label: 'Total Jobs',
      value: stats.total_jobs ?? 0,
      accent: 'from-blue-500 to-blue-400',
      textColor: 'text-blue-400',
      iconBg: 'bg-blue-500/10',
      icon: (
        <svg className="w-5 h-5 text-blue-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M21 13.255A23.931 23.931 0 0112 15c-3.183 0-6.22-.62-9-1.745M16 6V4a2 2 0 00-2-2h-4a2 2 0 00-2 2v2m4 6h.01M5 20h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
        </svg>
      ),
    },
    {
      label: 'Applied',
      value: stats.jobs_by_status?.Applied ?? 0,
      accent: 'from-emerald-500 to-emerald-400',
      textColor: 'text-emerald-400',
      iconBg: 'bg-emerald-500/10',
      icon: (
        <svg className="w-5 h-5 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      ),
    },
    {
      label: 'Interviews',
      value: stats.jobs_by_status?.Interview ?? 0,
      accent: 'from-purple-500 to-purple-400',
      textColor: 'text-purple-400',
      iconBg: 'bg-purple-500/10',
      icon: (
        <svg className="w-5 h-5 text-purple-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
        </svg>
      ),
    },
    {
      label: 'Avg Score',
      value: Math.round(stats.avg_match_score ?? 0),
      accent:
        (stats.avg_match_score ?? 0) >= 85
          ? 'from-emerald-500 to-emerald-400'
          : (stats.avg_match_score ?? 0) >= 60
            ? 'from-amber-500 to-amber-400'
            : 'from-red-500 to-red-400',
      textColor:
        (stats.avg_match_score ?? 0) >= 85
          ? 'text-emerald-400'
          : (stats.avg_match_score ?? 0) >= 60
            ? 'text-amber-400'
            : 'text-red-400',
      iconBg:
        (stats.avg_match_score ?? 0) >= 85
          ? 'bg-emerald-500/10'
          : (stats.avg_match_score ?? 0) >= 60
            ? 'bg-amber-500/10'
            : 'bg-red-500/10',
      icon: (
        <svg className={`w-5 h-5 ${(stats.avg_match_score ?? 0) >= 85 ? 'text-emerald-400' : (stats.avg_match_score ?? 0) >= 60 ? 'text-amber-400' : 'text-red-400'}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
        </svg>
      ),
    },
  ];

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
      {cards.map((c) => (
        <div
          key={c.label}
          className="relative bg-slate-800/80 border border-slate-700 rounded-lg p-5 overflow-hidden
            hover:border-slate-600 transition-colors group"
        >
          {/* Accent bar at bottom */}
          <div className={`absolute bottom-0 left-0 right-0 h-0.5 bg-gradient-to-r ${c.accent} opacity-60 group-hover:opacity-100 transition-opacity`} />

          <div className="flex items-start justify-between">
            <div>
              <p className="text-[11px] font-medium text-slate-400 uppercase tracking-wider mb-2">
                {c.label}
              </p>
              <p className={`text-3xl font-bold font-mono ${c.textColor}`}>
                {c.value}
              </p>
            </div>
            <div className={`${c.iconBg} rounded-lg p-2`}>
              {c.icon}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
