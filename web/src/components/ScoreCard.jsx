import { ScoreBadge } from './ui/Badge';

function ScoreWithLabel({ score, label }) {
  return (
    <div className="flex flex-col items-center gap-1">
      <ScoreBadge score={score} className="text-lg" />
      <span className="text-[10px] text-stone-400 font-mono uppercase tracking-wider">{label}</span>
    </div>
  );
}

export default function ScoreCard({ data, company, onRescore, rescoring }) {
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

      {/* A reused score has to say so.
          "if I press save and score on a job multiple times the scores are
          different" — 2026-10-08. They differed because a different MODEL
          answered each time: the AI cache is a SQLite file in the Lambda's
          /tmp and the rate-limit state is in-memory, so each container picks
          the first provider IT believes is usable. The endpoint now returns
          the score already on record for a job, which makes repeats exact.
          Saying nothing would be the other failure though — the user would see
          an instant, identical result and have no way to tell a stored score
          from a fresh one, or to ask for a new one. */}
      {data.reused && (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-2 border-stone-400 bg-stone-100 px-3 py-2">
          <p className="font-mono text-xs text-stone-600">
            Already scored — this is the stored score, so it will not drift.
          </p>
          {onRescore && (
            <button
              type="button"
              onClick={onRescore}
              disabled={rescoring}
              className="border-2 border-black bg-white px-2 py-0.5 font-mono text-[11px] font-bold uppercase hover:bg-stone-200 disabled:opacity-50"
            >
              {rescoring ? 'Scoring...' : 'Score again'}
            </button>
          )}
        </div>
      )}

      {/* /api/score returns `saved` precisely so a scored-but-not-written job
          can be told from a saved one. Without this the user gets an ordinary
          green Score Card, goes looking for the job on the dashboard, and
          finds nothing — the same silent failure the backend fix removed, one
          layer up. `saved` is undefined for a response from before that field
          existed, so only an explicit false is treated as a failure. */}
      {data.saved === false ? (
        <p
          role="status"
          className="mt-3 border-2 border-warning bg-warning-light px-3 py-2 text-xs font-mono text-warning-dark"
        >
          Scored, but <strong>not saved</strong> — this job will not appear on your
          dashboard. The score above is still valid; try again, or check the API logs.
        </p>
      ) : data.job_id ? (
        <p className="mt-3 text-xs font-mono text-success-dark">
          Saved to your dashboard.
        </p>
      ) : null}
    </div>
  );
}
