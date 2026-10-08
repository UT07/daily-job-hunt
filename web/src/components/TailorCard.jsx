import { ScoreBadge } from './ui/Badge';
import Button from './ui/Button';
import { Link } from 'react-router-dom';

function ScoreWithLabel({ score, label }) {
  return (
    <div className="flex flex-col items-center gap-1">
      <ScoreBadge score={score} className="text-lg" />
      <span className="text-[10px] text-stone-400 font-mono uppercase tracking-wider">{label}</span>
    </div>
  );
}

/**
 * The one place both pipeline cards explain a non-ready outcome. Every state
 * loadPipelineJob can return that is not 'ready' says what happened; none of
 * them reads as "still working", because by the time a card renders the
 * execution has already ended.
 */
export function PipelineOutcomeMessage({ result, what }) {
  const { state, reason } = result || {};
  const messages = {
    failed: `The ${what} failed to generate.`,
    not_saved: `The pipeline finished but nothing was saved for this job, so there is no ${what}.`,
    not_found: `The pipeline finished, but the saved job could not be found on your dashboard.`,
    load_error: `The pipeline finished, but loading the result failed.`,
    bad_output: `The pipeline finished without a usable result.`,
  };
  return (
    <div role="alert" className="text-sm border-2 border-error bg-error-light text-error p-3 font-mono">
      <p className="font-bold">{messages[state] || `No ${what} was produced.`}</p>
      {reason && <p className="mt-1 break-words">{reason}</p>}
    </div>
  );
}

export default function TailorCard({ data, company }) {
  const job = data?.job || null;
  const ready = data?.state === 'ready';
  const link = job ? (job.resume_s3_download_url || job.resume_s3_url || null) : null;

  return (
    <div data-testid="tailor-card" className="animate-fade-in border-2 border-black shadow-brutal bg-white p-5">
      <h3 className="text-xs font-bold text-stone-500 uppercase tracking-wider font-mono mb-4">
        Tailored Resume — {company}
      </h3>
      {ready && job && (
        <div className="flex gap-6 items-end mb-4">
          <ScoreWithLabel score={job.tailored_ats_score ?? job.ats_score} label="ATS" />
          <ScoreWithLabel score={job.tailored_hm_score ?? job.hiring_manager_score} label="Hiring Mgr" />
          <ScoreWithLabel score={job.tailored_tr_score ?? job.tech_recruiter_score} label="Tech Recruiter" />
        </div>
      )}
      {!ready && <PipelineOutcomeMessage result={data} what="résumé" />}
      <div className="flex items-center gap-3 mt-3">
        {ready && (link ? (
          <a href={link} target="_blank" rel="noopener noreferrer">
            <Button variant="secondary" size="sm">Download Resume PDF</Button>
          </a>
        ) : (
          <p role="alert" className="text-sm text-error font-mono">
            The pipeline finished but no résumé PDF is stored for this job.
          </p>
        ))}
        {job?.job_id && (
          <Link to={`/jobs/${job.job_id}`}>
            <Button variant="ghost" size="sm">View in Dashboard</Button>
          </Link>
        )}
      </div>
    </div>
  );
}
