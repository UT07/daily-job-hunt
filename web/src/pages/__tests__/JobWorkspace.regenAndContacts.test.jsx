/**
 * JobWorkspace — two outcomes that the page computed and then never showed.
 *
 * 1. Regenerate succeeded but the post-success refresh failed. pollRegeneration
 *    returns { outcome: 'succeeded', warning } and handleRegen stored the
 *    warning in `regenNotice` — which was rendered INSIDE the
 *    `(regenError || restoreError) &&` block, so on the success path (no error)
 *    the notice was never in the DOM.
 *
 * 2. Find Contacts ran the task to completion and ignored its result. The
 *    worker (app.py `task_type == "contacts"`) writes `jobs.linkedin_contacts`
 *    and returns `{ contacts: [...], job_id }`; the tab rendered from the `job`
 *    prop, which nothing refetched, so new contacts appeared only on reload.
 *
 * Doubles follow app.py: POST /api/pipeline/re-tailor returns `pollUrl`,
 * GET /api/pipeline/status/{exec} returns `{status}`, and apiCall resolves a
 * polled task to the task's `result`.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const { apiGet, apiPatch, apiCall } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPatch: vi.fn(),
  apiCall: vi.fn(),
}));
vi.mock('../../api', () => ({ apiGet, apiPatch, apiCall }));

vi.mock('react-router-dom', () => ({
  useParams: () => ({ jobId: 'job-1' }),
  useNavigate: () => vi.fn(),
}));

vi.mock('../../components/EmailComposer', () => ({
  default: () => <div data-testid="email-composer-stub" />,
}));

import JobWorkspace from '../JobWorkspace';

const JOB = {
  job_id: 'job-1',
  title: 'Backend Engineer',
  company: 'Acme Corp',
  location: 'Dublin',
  application_status: 'New',
  description: 'Build things.',
  match_score: 88,
  resume_s3_url: 'https://s3.example/resume.pdf?sig=1',
  resume_version: 1,
  linkedin_contacts: null,
};

const CONTACT = {
  name: 'Jane Recruiter',
  role: 'Technical Recruiter',
  profile_url: 'https://linkedin.com/in/jane',
  why: 'Recruits for backend roles',
  message: 'Hi Jane',
};

beforeEach(() => {
  apiGet.mockReset();
  apiPatch.mockReset();
  apiCall.mockReset();
});

describe('Resume tab regenerate notice', () => {
  it('shows the "regenerated, but could not refresh" notice on the success path', async () => {
    let jobFetches = 0;
    apiGet.mockImplementation((url) => {
      if (url === '/api/dashboard/jobs/job-1') {
        jobFetches += 1;
        // First fetch is the page load; the post-regen refresh fails.
        if (jobFetches === 1) return Promise.resolve({ ...JOB });
        return Promise.reject(new Error('HTTP 502'));
      }
      if (url === '/api/pipeline/status/exec-1') return Promise.resolve({ status: 'SUCCEEDED' });
      return Promise.resolve([]);
    });
    apiCall.mockResolvedValue({ pollUrl: '/api/pipeline/status/exec-1' });

    render(<JobWorkspace />);
    fireEvent.click(await screen.findByText('Resume'));
    fireEvent.click(screen.getByText('Regenerate'));

    expect(
      await screen.findByText(/Regenerated, but could not refresh the page \(HTTP 502\)/),
    ).toBeInTheDocument();
    // It is a notice, not a failure.
    expect(screen.queryByText(/Regenerate failed/)).not.toBeInTheDocument();
  });

  it('shows a cover-letter regenerate failure on the Cover Letter tab', async () => {
    apiGet.mockImplementation((url) => {
      if (url === '/api/dashboard/jobs/job-1') {
        return Promise.resolve({ ...JOB, cover_letter_s3_url: 'https://s3.example/cl.pdf' });
      }
      if (url === '/api/pipeline/status/exec-1') {
        return Promise.resolve({ status: 'FAILED', error: 'CoverLetterFailed' });
      }
      return Promise.resolve([]);
    });
    apiCall.mockResolvedValue({ pollUrl: '/api/pipeline/status/exec-1' });

    render(<JobWorkspace />);
    fireEvent.click(await screen.findByText('Cover Letter'));
    fireEvent.click(screen.getByText('Regenerate'));

    expect(await screen.findByText(/Regenerate failed: CoverLetterFailed/)).toBeInTheDocument();
  });
});

describe('Contacts tab', () => {
  it('shows contacts found by the task without a reload', async () => {
    let jobFetches = 0;
    apiGet.mockImplementation((url) => {
      if (url === '/api/dashboard/jobs/job-1') {
        jobFetches += 1;
        // The worker has written linkedin_contacts by the time the task is done.
        return Promise.resolve(
          jobFetches === 1 ? { ...JOB } : { ...JOB, linkedin_contacts: JSON.stringify([CONTACT]) },
        );
      }
      return Promise.resolve([]);
    });
    apiCall.mockResolvedValue({ contacts: [CONTACT], job_id: 'job-1' });

    render(<JobWorkspace />);
    fireEvent.click(await screen.findByText('Contacts'));
    expect(screen.getByText(/0 Contacts/)).toBeInTheDocument();
    fireEvent.click(screen.getByText('Find Contacts'));

    expect(await screen.findByText('Jane Recruiter')).toBeInTheDocument();
    expect(screen.getByText(/1 Contact /)).toBeInTheDocument();
  });

  it('falls back to the task result when the refetch fails', async () => {
    let jobFetches = 0;
    apiGet.mockImplementation((url) => {
      if (url === '/api/dashboard/jobs/job-1') {
        jobFetches += 1;
        if (jobFetches === 1) return Promise.resolve({ ...JOB });
        return Promise.reject(new Error('HTTP 502'));
      }
      return Promise.resolve([]);
    });
    apiCall.mockResolvedValue({ contacts: [CONTACT], job_id: 'job-1' });

    render(<JobWorkspace />);
    fireEvent.click(await screen.findByText('Contacts'));
    fireEvent.click(screen.getByText('Find Contacts'));

    expect(await screen.findByText('Jane Recruiter')).toBeInTheDocument();
  });

  it('says so when the search finished and found nobody', async () => {
    apiGet.mockImplementation((url) => {
      if (url === '/api/dashboard/jobs/job-1') return Promise.resolve({ ...JOB });
      return Promise.resolve([]);
    });
    apiCall.mockResolvedValue({ contacts: [], job_id: 'job-1' });

    render(<JobWorkspace />);
    fireEvent.click(await screen.findByText('Contacts'));
    fireEvent.click(screen.getByText('Find Contacts'));

    expect(await screen.findByText(/Search finished: no contacts found/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('Find Contacts')).toBeInTheDocument());
  });
});
