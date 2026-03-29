import { useState, useEffect } from 'react';
import { apiGet } from '../api';

export default function AIQualityStats() {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (open && !data) {
      setLoading(true);
      setError(null);
      apiGet('/api/quality-stats')
        .then(setData)
        .catch((err) => setError(err.message))
        .finally(() => setLoading(false));
    }
  }, [open, data]);

  const modelStats = data?.model_stats || {};
  const recentLogs = data?.recent_logs || [];
  const modelKeys = Object.keys(modelStats).sort(
    (a, b) => modelStats[b].count - modelStats[a].count
  );

  // Task label mapping
  const taskLabels = {
    match: 'Match',
    tailor_resume: 'Tailor',
    tailor_text: 'Tailor (text)',
    score_resume: 'Score',
    improve_resume: 'Improve',
    cover_letter: 'Cover Letter',
    find_contacts: 'Contacts',
  };

  return (
    <div className="glass rounded-lg border border-slate-700 overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-5 py-4 hover:bg-slate-800/50 transition-colors"
      >
        <div className="flex items-center gap-3">
          <div className="bg-violet-500/10 rounded-lg p-2">
            <svg className="w-5 h-5 text-violet-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
            </svg>
          </div>
          <span className="text-sm font-semibold text-slate-200">AI Quality Stats</span>
          <span className="text-[10px] text-slate-500 font-medium uppercase tracking-wider">LLM Traceability</span>
        </div>
        <svg
          className={`w-5 h-5 text-slate-400 transition-transform ${open ? 'rotate-180' : ''}`}
          fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div className="border-t border-slate-700 px-5 py-5">
          {loading && (
            <div className="flex items-center gap-3 py-6 justify-center">
              <span className="spinner" />
              <span className="text-slate-400 text-sm">Loading quality stats...</span>
            </div>
          )}

          {error && (
            <div className="bg-red-900/30 border border-red-800/50 text-red-300 text-sm rounded-lg p-3">
              {error}
            </div>
          )}

          {data && !loading && (
            <div className="space-y-6">
              {/* Per-model summary table */}
              {modelKeys.length > 0 ? (
                <div>
                  <h3 className="text-xs font-medium text-slate-400 uppercase tracking-wider mb-3">Per-Model Performance</h3>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-left text-[11px] text-slate-500 uppercase tracking-wider">
                          <th className="pb-2 pr-4">Model</th>
                          <th className="pb-2 pr-4 text-right">Calls</th>
                          <th className="pb-2 pr-4 text-right">Avg Score</th>
                          <th className="pb-2 pr-4 text-right">Errors</th>
                          <th className="pb-2">Tasks</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-700/50">
                        {modelKeys.map((key) => {
                          const s = modelStats[key];
                          const scoreColor =
                            s.avg_score >= 85
                              ? 'text-emerald-400'
                              : s.avg_score >= 60
                                ? 'text-amber-400'
                                : s.avg_score > 0
                                  ? 'text-red-400'
                                  : 'text-slate-500';
                          return (
                            <tr key={key} className="text-slate-300">
                              <td className="py-2 pr-4 font-mono text-xs text-violet-300">{key}</td>
                              <td className="py-2 pr-4 text-right font-mono">{s.count}</td>
                              <td className={`py-2 pr-4 text-right font-mono font-semibold ${scoreColor}`}>
                                {s.avg_score > 0 ? s.avg_score : '--'}
                              </td>
                              <td className={`py-2 pr-4 text-right font-mono ${s.errors > 0 ? 'text-red-400' : 'text-slate-500'}`}>
                                {s.errors}
                              </td>
                              <td className="py-2">
                                <div className="flex flex-wrap gap-1.5">
                                  {Object.entries(s.tasks || {}).map(([task, count]) => (
                                    <span
                                      key={task}
                                      className="inline-flex items-center gap-1 bg-slate-800 border border-slate-600 rounded px-1.5 py-0.5 text-[10px] text-slate-400"
                                    >
                                      {taskLabels[task] || task}
                                      <span className="text-slate-500 font-mono">{count}</span>
                                    </span>
                                  ))}
                                </div>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              ) : (
                <p className="text-slate-500 text-sm">No AI quality data yet. Run the pipeline or use the tailor/score features to generate data.</p>
              )}

              {/* Recent log entries */}
              {recentLogs.length > 0 && (
                <div>
                  <h3 className="text-xs font-medium text-slate-400 uppercase tracking-wider mb-3">
                    Recent AI Operations ({recentLogs.length})
                  </h3>
                  <div className="max-h-64 overflow-y-auto space-y-1.5 pr-1">
                    {recentLogs.slice().reverse().map((entry, i) => {
                      const scores = entry.scores || {};
                      const hasScores = Object.keys(scores).length > 0;
                      const avgScore = hasScores
                        ? Math.round(
                            Object.values(scores).reduce((a, b) => a + b, 0) /
                              Object.values(scores).length
                          )
                        : null;
                      const scoreColor =
                        avgScore >= 85
                          ? 'text-emerald-400'
                          : avgScore >= 60
                            ? 'text-amber-400'
                            : avgScore !== null
                              ? 'text-red-400'
                              : '';
                      return (
                        <div
                          key={i}
                          className="flex items-center gap-3 bg-slate-800/50 border border-slate-700/50 rounded px-3 py-2 text-xs"
                        >
                          <span className="bg-slate-700 rounded px-1.5 py-0.5 text-[10px] text-slate-300 font-medium min-w-[70px] text-center">
                            {taskLabels[entry.task] || entry.task}
                          </span>
                          <span className="font-mono text-violet-300 text-[11px] truncate max-w-[180px]">
                            {entry.provider}:{entry.model}
                          </span>
                          {entry.company && (
                            <span className="text-slate-400 truncate max-w-[120px]">
                              {entry.company}
                            </span>
                          )}
                          {avgScore !== null && (
                            <span className={`font-mono font-semibold ml-auto ${scoreColor}`}>
                              {avgScore}
                            </span>
                          )}
                          {!entry.success && (
                            <span className="text-red-400 ml-auto font-medium">FAILED</span>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
