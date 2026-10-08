import Button from './ui/Button';
import { PipelineOutcomeMessage } from './TailorCard';

/**
 * Renders from the saved job row (see pages/pipelineJobResult.js), not the
 * execution output, which carries no document url.
 *
 * save_job's `failed` describes the RÉSUMÉ compile; a cover-letter failure is
 * non-fatal (SaveJobWithoutCL) and shows up only as the row having no
 * cover_letter_s3_url. So the link is the test here, and its absence is
 * reported as such rather than as "in progress".
 */
export default function CoverLetterCard({ data, company }) {
  const job = data?.job || null;
  const link = job ? (job.cover_letter_s3_download_url || job.cover_letter_s3_url || null) : null;

  let body;
  if (link) {
    body = (
      <a href={link} target="_blank" rel="noopener noreferrer">
        <Button variant="secondary" size="sm">Download Cover Letter PDF</Button>
      </a>
    );
  } else if (data?.state && data.state !== 'ready' && data.state !== 'failed') {
    body = <PipelineOutcomeMessage result={data} what="cover letter" />;
  } else {
    body = (
      <p role="alert" className="text-sm text-error font-mono">
        The pipeline finished but no cover letter was produced for this job.
        {data?.reason ? ` ${data.reason}` : ''}
      </p>
    );
  }

  return (
    <div className="animate-fade-in border-2 border-black shadow-brutal bg-white p-5">
      <h3 className="text-xs font-bold text-stone-500 uppercase tracking-wider font-mono mb-4">
        Cover Letter — {company}
      </h3>
      {body}
    </div>
  );
}
