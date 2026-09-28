/**
 * The Studio must open even when generation has not run.
 *
 * The spec is explicit: "On failure the editor still opens, empty, with the
 * error and a retry — the Studio is the destination." GET .../sections returns
 * 404 for any job with no tailored .tex, which is the common case for a job
 * the user just scored. A Studio that renders a blank screen on 404 would be
 * unreachable for exactly the jobs it is most useful for.
 */
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ResumeStudio from '../ResumeStudio';
import * as api from '../../api';

const JOB = { job_id: 'j1', title: 'Platform Engineer', company: 'Acme' };

function renderStudio() {
  return render(
    <MemoryRouter initialEntries={['/jobs/j1/studio']}>
      <Routes>
        <Route path="/jobs/:jobId/studio" element={<ResumeStudio />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('ResumeStudio shell', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('shows the job title and company once loaded', async () => {
    vi.spyOn(api, 'apiGet').mockImplementation((url) =>
      url.endsWith('/sections')
        ? Promise.resolve({ sections: { summary: 'hi' }, jd_analysis: {} })
        : Promise.resolve(JOB),
    );
    renderStudio();
    await waitFor(() => expect(screen.getByText(/Platform Engineer/)).toBeInTheDocument());
    expect(screen.getByText(/Acme/)).toBeInTheDocument();
  });

  it('still opens when no tailored resume exists yet', async () => {
    vi.spyOn(api, 'apiGet').mockImplementation((url) =>
      url.endsWith('/sections')
        ? Promise.reject(new Error('No tailored .tex found for job j1. Run tailoring first.'))
        : Promise.resolve(JOB),
    );
    renderStudio();
    await waitFor(() => expect(screen.getByRole('status')).toBeInTheDocument());
    expect(screen.getByRole('status')).toHaveTextContent(/no tailored resume/i);
    // The destination still rendered — the header is present, not a blank page.
    expect(screen.getByText(/Platform Engineer/)).toBeInTheDocument();
  });
});
