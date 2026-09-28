/**
 * The Studio must open even when generation has not run.
 *
 * The spec is explicit: "On failure the editor still opens, empty, with the
 * error and a retry — the Studio is the destination." GET .../sections returns
 * 404 for any job with no tailored .tex, which is the common case for a job
 * the user just scored. A Studio that renders a blank screen on 404 would be
 * unreachable for exactly the jobs it is most useful for.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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
    // Queried by text, not by role: the editor pane also renders a role=status
    // empty state, and both messages are legitimate — the banner explains WHY
    // there is nothing, the editor says what to do about it.
    await waitFor(() => expect(screen.getByText(/no tailored resume/i)).toBeInTheDocument());
    // The destination still rendered — the header is present, not a blank page.
    expect(screen.getByText(/Platform Engineer/)).toBeInTheDocument();
  });
});

describe('ResumeStudio composition', () => {
  const FULL_JOB = {
    job_id: 'j1', title: 'Platform Engineer', company: 'Acme',
    ats_score: 86, hiring_manager_score: 84, tech_recruiter_score: 90,
    key_matches: ['Kubernetes'], gaps: ['Go'],
    requirement_map: [{ requirement: 'run Kubernetes', evidence: 'did', severity: 'met' }],
    resume_s3_url: 'https://s3/existing.pdf',
  };
  const SECTIONS = {
    header: { name: 'Jane', title: 'SRE', contact: 'j@x.com' },
    summary: 'Platform engineer.', skills: [], experience: [],
    projects: [], education: [], certifications: [],
  };

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, 'apiGet').mockImplementation((url) =>
      url.endsWith('/sections')
        ? Promise.resolve({ sections: SECTIONS, jd_analysis: {} })
        : Promise.resolve(FULL_JOB));
  });

  it('shows coverage, scores, the editor and the PDF together', async () => {
    renderStudio();
    await waitFor(() => expect(screen.getByLabelText('JD coverage')).toBeInTheDocument());
    expect(screen.getByTestId('score-band')).toHaveTextContent('84–90');
    expect(screen.getByLabelText(/summary/i)).toHaveValue('Platform engineer.');
    expect(screen.getByTitle(/resume preview/i)).toHaveAttribute('src', 'https://s3/existing.pdf');
  });

  it('compiles on blur, once, through the sections endpoint', async () => {
    const apiCall = vi.spyOn(api, 'apiCall').mockResolvedValue({ task_id: 't1', poll_url: '/api/tasks/t1' });
    vi.spyOn(api, 'pollPipeline').mockResolvedValue({ status: 'done', result: { pdf_url: 'https://s3/new.pdf' } });

    renderStudio();
    await waitFor(() => expect(screen.getByLabelText(/summary/i)).toBeInTheDocument());

    const summary = screen.getByLabelText(/summary/i);
    fireEvent.change(summary, { target: { value: 'Edited summary.' } });
    fireEvent.blur(summary);

    await waitFor(() => expect(apiCall).toHaveBeenCalledTimes(1));
    expect(apiCall).toHaveBeenCalledWith(
      '/api/dashboard/jobs/j1/sections',
      expect.objectContaining({ sections: expect.objectContaining({ summary: 'Edited summary.' }) }),
    );
  });

  it('does not compile when a blur changed nothing', async () => {
    const apiCall = vi.spyOn(api, 'apiCall').mockResolvedValue({ task_id: 't1', poll_url: '/api/tasks/t1' });
    renderStudio();
    await waitFor(() => expect(screen.getByLabelText(/summary/i)).toBeInTheDocument());
    fireEvent.blur(screen.getByLabelText(/summary/i));
    await new Promise((r) => setTimeout(r, 0));
    expect(apiCall).not.toHaveBeenCalled();
  });

  it('exposes no LaTeX anywhere on the page', async () => {
    const { container } = renderStudio();
    await waitFor(() => expect(screen.getByLabelText(/summary/i)).toBeInTheDocument());
    expect(container.textContent).not.toMatch(/\\documentclass|\\begin\{document\}|tex_s3_key/);
  });
});
