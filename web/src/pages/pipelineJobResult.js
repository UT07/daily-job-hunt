/**
 * Turn a finished single-job pipeline run into something a card can render.
 *
 * The single-job state machine ends in SaveJobComplete / SaveJobWithoutCL
 * (template.yaml), so the execution output pollPipeline resolves with is
 * save_job.handler's return value and nothing else:
 *
 *     {job_hash, user_id, saved, has_resume, failed}
 *
 * There is no PDF url and no score in it. The cards used to read pdf_url /
 * drive_url / ats_score off this object, found none of them, and showed "--"
 * and "PDF generation in progress..." forever -- including when `failed` was
 * true. The documents and scores live on the saved job row, so we fetch it.
 *
 * GET /api/dashboard/jobs has no job_hash filter, so the lookup narrows by
 * company + title (ilike) across every lifecycle and then matches job_hash
 * exactly. A row we cannot find is reported as such, never as "in progress".
 *
 * Returns { state, job, reason } where state is one of:
 *   'ready'        saved, résumé compiled, row found
 *   'failed'       save_job reported failed (compile failed); reason from the row
 *   'not_saved'    save_job wrote nothing
 *   'not_found'    saved, but no row with this job_hash came back
 *   'load_error'   the lookup request itself failed
 *   'bad_output'   the output is not save_job's shape
 */
export async function loadPipelineJob(output, { company, title } = {}, get) {
  if (!output || typeof output !== 'object' || typeof output.job_hash !== 'string') {
    return { state: 'bad_output', job: null, reason: 'The pipeline returned no job reference.' };
  }
  if (output.saved === false) {
    return { state: 'not_saved', job: null, reason: null };
  }

  const params = new URLSearchParams({ lifecycle: 'all', per_page: '100' });
  if (company && company.trim()) params.set('company', company.trim());
  if (title && title.trim()) params.set('title', title.trim());

  let job = null;
  try {
    const res = await get(`/api/dashboard/jobs?${params.toString()}`);
    const rows = Array.isArray(res?.jobs) ? res.jobs : [];
    job = rows.find((r) => r.job_hash === output.job_hash || r.canonical_hash === output.job_hash) || null;
  } catch (e) {
    if (output.failed) {
      return { state: 'failed', job: null, reason: null };
    }
    return { state: 'load_error', job: null, reason: e?.message || 'request failed' };
  }

  if (output.failed) {
    return { state: 'failed', job, reason: job?.failure_reason || null };
  }
  if (!job) {
    return { state: 'not_found', job: null, reason: null };
  }
  return { state: 'ready', job, reason: null };
}
