/**
 * AddJob used to hold jd/jobTitle/company/location/applyUrl/resumeType in
 * plain useState with no persistence, so navigating away from the page threw
 * away a job description the user had just pasted (2026-09-28 report).
 *
 * The draft now round-trips through sessionStorage, following the shape
 * Dashboard already uses for its filters (a DEFAULTS map + a per-field reader
 * in lazy useState initialisers + one write-back effect) — sessionStorage
 * rather than the query string because a JD is far too long for a URL and has
 * no business in browser history.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const { apiCall, pollPipeline, apiGet } = vi.hoisted(() => ({
  apiCall: vi.fn(),
  pollPipeline: vi.fn(),
  apiGet: vi.fn(),
}));
vi.mock('../../api', () => ({ apiCall, pollPipeline, apiGet }));

import AddJob from '../AddJob';

const STORAGE_KEY = 'naukribaba_addjob_draft';
const JD = 'We are hiring a Site Reliability Engineer to own our Kubernetes platform.';

describe('AddJob draft persistence', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
  });

  it('restores everything the user typed after navigating away and back', () => {
    const { unmount } = render(<AddJob />);

    fireEvent.change(screen.getByLabelText('Job Description'), { target: { value: JD } });
    fireEvent.change(screen.getByLabelText('Job Title'), { target: { value: 'SRE' } });
    fireEvent.change(screen.getByLabelText('Company'), { target: { value: 'Stripe' } });
    fireEvent.change(screen.getByLabelText('Location (optional)'), { target: { value: 'Dublin' } });
    fireEvent.change(screen.getByLabelText('Apply URL (optional)'), { target: { value: 'https://x.test/1' } });
    fireEvent.change(screen.getByLabelText('Resume Type'), { target: { value: 'fullstack' } });

    // Navigate away.
    unmount();

    // ...and back.
    render(<AddJob />);
    expect(screen.getByLabelText('Job Description')).toHaveValue(JD);
    expect(screen.getByLabelText('Job Title')).toHaveValue('SRE');
    expect(screen.getByLabelText('Company')).toHaveValue('Stripe');
    expect(screen.getByLabelText('Location (optional)')).toHaveValue('Dublin');
    expect(screen.getByLabelText('Apply URL (optional)')).toHaveValue('https://x.test/1');
    expect(screen.getByLabelText('Resume Type')).toHaveValue('fullstack');
  });

  it('persists only the fields that differ from their defaults', () => {
    render(<AddJob />);

    fireEvent.change(screen.getByLabelText('Job Description'), { target: { value: JD } });

    // Job Title is still 'Software Engineer' and Resume Type still
    // 'sre_devops', so neither should be written — same rule Dashboard's URL
    // sync applies to keep the query string clean.
    expect(JSON.parse(sessionStorage.getItem(STORAGE_KEY))).toEqual({ jd: JD });
  });

  it('leaves no draft behind for a pristine form', () => {
    render(<AddJob />);
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it('clears the stored draft once the user empties the form again', () => {
    render(<AddJob />);

    const jdField = screen.getByLabelText('Job Description');
    fireEvent.change(jdField, { target: { value: JD } });
    expect(sessionStorage.getItem(STORAGE_KEY)).not.toBeNull();

    fireEvent.change(jdField, { target: { value: '' } });
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it('falls back to an empty form when the stored draft is corrupt', () => {
    sessionStorage.setItem(STORAGE_KEY, 'not json{');

    render(<AddJob />);

    expect(screen.getByLabelText('Job Description')).toHaveValue('');
    expect(screen.getByLabelText('Job Title')).toHaveValue('Software Engineer');
  });

  it('ignores non-string values in a stored draft rather than rendering them', () => {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ jd: { nope: true }, company: 'Stripe' }));

    render(<AddJob />);

    expect(screen.getByLabelText('Job Description')).toHaveValue('');
    expect(screen.getByLabelText('Company')).toHaveValue('Stripe');
  });
});

/**
 * What Tailor Resume / Cover Letter show when the pipeline finishes.
 *
 * pollPipeline resolves with the Step Function's output, and the single-job
 * machine ends in SaveJobComplete / SaveJobWithoutCL (template.yaml), so the
 * output IS save_job.handler's return value:
 *
 *     {job_hash, user_id, saved, has_resume, failed}
 *
 * (lambdas/pipeline/save_job.py, both return statements). The cards used to
 * read pdf_url / drive_url / ats_score straight off that object; none of
 * those keys exist in it, so every run rendered "--" scores and "PDF
 * generation in progress..." forever, and `failed: true` read the same as
 * success. These tests use the real shape; the earlier suite never resolved
 * pollPipeline at all, which is how this went unseen.
 */
const SAVE_JOB_OK = {
  job_hash: 'b3026c307c74', user_id: 'u1', saved: true, has_resume: true, failed: false,
};

// A row as GET /api/dashboard/jobs returns it (JOB_LIST_COLUMNS, after
// _refresh_s3_urls has set the presigned urls).
const JOB_ROW = {
  job_id: 'job-uuid-1',
  job_hash: 'b3026c307c74',
  company: 'Grinds360',
  title: 'DevOps Engineer',
  ats_score: 70, hiring_manager_score: 72, tech_recruiter_score: 68,
  tailored_ats_score: 91, tailored_hm_score: 88, tailored_tr_score: 86,
  resume_s3_key: 'u1/b3026c307c74/resume.pdf',
  resume_s3_url: 'https://s3.test/resume.pdf?sig=inline',
  resume_s3_download_url: 'https://s3.test/resume.pdf?sig=attachment',
  cover_letter_s3_url: 'https://s3.test/cl.pdf?sig=inline',
  failure_reason: null,
};

function fillAndRun(button) {
  render(<MemoryRouter><AddJob /></MemoryRouter>);
  fireEvent.change(screen.getByLabelText('Job Description'), { target: { value: JD } });
  fireEvent.change(screen.getByLabelText('Job Title'), { target: { value: 'DevOps Engineer' } });
  fireEvent.change(screen.getByLabelText('Company'), { target: { value: 'Grinds360' } });
  fireEvent.click(screen.getByRole('button', { name: button }));
}

describe('AddJob pipeline result cards read the real SaveJob output', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    apiCall.mockResolvedValue({ pollUrl: '/api/pipeline/status/exec-1' });
  });

  it('fetches the saved job and links its résumé PDF and tailored scores', async () => {
    pollPipeline.mockResolvedValue(SAVE_JOB_OK);
    apiGet.mockResolvedValue({ jobs: [{ ...JOB_ROW, job_hash: 'other' }, JOB_ROW], total: 2 });

    fillAndRun('Tailor Resume');

    const link = await screen.findByRole('link', { name: /download resume pdf/i });
    expect(link).toHaveAttribute('href', JOB_ROW.resume_s3_download_url);
    expect(screen.getByText('91')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /view in dashboard/i }))
      .toHaveAttribute('href', '/jobs/job-uuid-1');
    expect(screen.queryByText(/in progress/i)).not.toBeInTheDocument();
    // Looked up through the dashboard list endpoint, all lifecycles.
    expect(apiGet.mock.calls[0][0]).toMatch(/^\/api\/dashboard\/jobs\?/);
    expect(apiGet.mock.calls[0][0]).toContain('lifecycle=all');
  });

  it('shows failure, not "in progress", when SaveJob reports failed: true', async () => {
    pollPipeline.mockResolvedValue({ ...SAVE_JOB_OK, has_resume: false, failed: true });
    apiGet.mockResolvedValue({
      jobs: [{ ...JOB_ROW, resume_s3_url: null, resume_s3_download_url: null,
        failure_reason: 'tectonic: Undefined control sequence' }],
      total: 1,
    });

    fillAndRun('Tailor Resume');

    expect(await screen.findByText(/résumé failed/i)).toBeInTheDocument();
    expect(await screen.findByText(/Undefined control sequence/)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /download resume pdf/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/in progress/i)).not.toBeInTheDocument();
  });

  it('shows failure when SaveJob reports saved: false', async () => {
    pollPipeline.mockResolvedValue({
      job_hash: 'b3026c307c74', user_id: 'u1', saved: false, has_resume: false, failed: false,
    });
    apiGet.mockResolvedValue({ jobs: [], total: 0 });

    fillAndRun('Tailor Resume');

    expect(await screen.findByText(/nothing was saved/i)).toBeInTheDocument();
    expect(screen.queryByText(/in progress/i)).not.toBeInTheDocument();
  });

  it('cover letter card links the stored cover letter', async () => {
    pollPipeline.mockResolvedValue(SAVE_JOB_OK);
    apiGet.mockResolvedValue({ jobs: [JOB_ROW], total: 1 });

    fillAndRun('Cover Letter');

    const link = await screen.findByRole('link', { name: /download cover letter pdf/i });
    expect(link).toHaveAttribute('href', JOB_ROW.cover_letter_s3_url);
  });

  it('cover letter card says so when the run produced no cover letter', async () => {
    // SaveJobWithoutCL: the CL step failed non-fatally; the row has a résumé only.
    pollPipeline.mockResolvedValue(SAVE_JOB_OK);
    apiGet.mockResolvedValue({ jobs: [{ ...JOB_ROW, cover_letter_s3_url: null }], total: 1 });

    fillAndRun('Cover Letter');

    expect(await screen.findByText(/no cover letter was produced/i)).toBeInTheDocument();
    expect(screen.queryByText(/in progress/i)).not.toBeInTheDocument();
  });

  it('a FAILED execution surfaces its error and cause', async () => {
    const err = new Error('JobProcessingFailed: Failed to process job');
    pollPipeline.mockRejectedValue(err);

    fillAndRun('Tailor Resume');

    await waitFor(() =>
      expect(screen.getByText(/JobProcessingFailed: Failed to process job/)).toBeInTheDocument());
  });
});
