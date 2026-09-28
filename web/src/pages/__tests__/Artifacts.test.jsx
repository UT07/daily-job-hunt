/**
 * The Artifacts page.
 *
 * Built because 23 of 25 active jobs had a tailored resume and there was
 * nowhere to see them — reported as "why are there no artifacts at all".
 * The page's real job is showing what is MISSING against the tier policy,
 * not just listing what exists.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../api', () => ({ apiGet: vi.fn() }));
import { apiGet } from '../../api';
import Artifacts from '../Artifacts';

const R = 'https://s3.example.com/r.pdf';
const C = 'https://s3.example.com/c.pdf';

const renderPage = () => render(<MemoryRouter><Artifacts /></MemoryRouter>);

beforeEach(() => vi.clearAllMocks());

describe('Artifacts page', () => {
  it('requests only the tiers the policy covers, and only active jobs', async () => {
    apiGet.mockResolvedValue({ jobs: [] });
    renderPage();
    await waitFor(() => expect(apiGet).toHaveBeenCalled());
    const url = apiGet.mock.calls[0][0];
    const params = new URLSearchParams(url.split('?')[1]);
    expect(params.get('tier')).toBe('S,A,B');
    // not the backend's not_archived default, which also returns the stale band
    expect(params.get('lifecycle')).toBe('active');
  });

  it('offers a download for artifacts that exist', async () => {
    apiGet.mockResolvedValue({ jobs: [
      { job_id: '1', title: 'SRE', company: 'Twilio', score_tier: 'S', match_score: 91,
        resume_s3_url: R, cover_letter_s3_url: C },
    ] });
    renderPage();
    const resume = await screen.findByRole('link', { name: /resume/i });
    expect(resume).toHaveAttribute('href', R);
    expect(screen.getByRole('link', { name: /cover letter/i })).toHaveAttribute('href', C);
  });

  it('names what is missing rather than silently omitting it', async () => {
    apiGet.mockResolvedValue({ jobs: [
      { job_id: '2', title: 'Backend', company: 'Gitlab', score_tier: 'A', match_score: 80,
        resume_s3_url: R },
    ] });
    renderPage();
    expect(await screen.findByText(/cover letter missing/i)).toBeInTheDocument();
    expect(screen.getByText(/1 incomplete/i)).toBeInTheDocument();
  });

  it('does not ask a B-tier job for a cover letter', async () => {
    apiGet.mockResolvedValue({ jobs: [
      { job_id: '3', title: 'Platform', company: 'Linear', score_tier: 'B', match_score: 71,
        resume_s3_url: R },
    ] });
    renderPage();
    await screen.findByRole('link', { name: /resume/i });
    // Scoped to the row, not the document: the page's own subtitle explains
    // the policy and legitimately mentions cover letters.
    const row = screen.getByRole('listitem');
    expect(within(row).queryByText(/cover letter/i)).not.toBeInTheDocument();
    expect(screen.getByText(/all set/i)).toBeInTheDocument();
  });

  it('counts ready-to-send against the applicable total', async () => {
    apiGet.mockResolvedValue({ jobs: [
      { job_id: '1', score_tier: 'S', title: 'a', company: 'x', resume_s3_url: R, cover_letter_s3_url: C },
      { job_id: '2', score_tier: 'A', title: 'b', company: 'y', resume_s3_url: R },
      { job_id: '3', score_tier: 'B', title: 'c', company: 'z', resume_s3_url: R },
    ] });
    renderPage();
    await waitFor(() => expect(screen.getByText('2')).toBeInTheDocument());
    expect(screen.getByText('/3')).toBeInTheDocument();
  });

  it('surfaces a load failure instead of rendering an empty page', async () => {
    apiGet.mockRejectedValue(new Error('backend down'));
    renderPage();
    expect(await screen.findByRole('alert')).toHaveTextContent(/backend down/i);
  });

  it('says so plainly when there is nothing to generate', async () => {
    apiGet.mockResolvedValue({ jobs: [] });
    renderPage();
    expect(await screen.findByText(/nothing to generate yet/i)).toBeInTheDocument();
  });
});
