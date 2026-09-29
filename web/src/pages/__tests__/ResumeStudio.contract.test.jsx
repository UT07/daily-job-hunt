/**
 * The compile path, exercised against the REAL apiCall — no api.js stubs.
 *
 * The branch's first version of these tests stubbed both `apiCall` and
 * `pollPipeline`, with shapes neither real function produces:
 *
 *     vi.spyOn(api, 'apiCall').mockResolvedValue({task_id, poll_url})
 *     vi.spyOn(api, 'pollPipeline').mockResolvedValue({status, result:{pdf_url}})
 *
 * Both were wrong, and they agreed with the code because the same wrong mental
 * model wrote both. A mock can only test your BELIEF about a boundary; it
 * cannot test the boundary. 36 tests passed while every compile in production
 * would have 404'd.
 *
 * What apiCall actually does (web/src/api.js:85):
 *     if (data.task_id && data.poll_url) return pollTask(data.poll_url, options)
 * and pollTask returns `task.result` — so apiCall ALREADY follows the 202 to
 * completion and hands back {job_id, tex_s3_key, pdf_s3_key, pdf_url}. There is
 * no second poll to do, and `poll_url` is not in the value it returns.
 *
 * So these tests stub `fetch` and let the real apiCall/pollTask run.
 */
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ResumeStudio from '../ResumeStudio';

const JOB = {
  job_id: 'j1', title: 'Platform Engineer', company: 'Acme',
  ats_score: 86, hiring_manager_score: 84, tech_recruiter_score: 90,
  key_matches: [], gaps: [], requirement_map: [],
  resume_s3_url: 'https://s3/before.pdf',
};
const SECTIONS = {
  header: { name: 'Jane', title: 'SRE', contact: 'j@x.com' },
  summary: 'Platform engineer.', skills: [], experience: [],
  projects: [{ company: 'P', title: 'T', dates: '2024', bullets: ['b'] }],
  education: [{ school: 'TCD' }], certifications: [],
};

function jsonRes(body) {
  return { ok: true, status: 200, json: async () => body };
}

function renderStudio() {
  return render(
    <MemoryRouter initialEntries={['/jobs/j1/studio']}>
      <Routes>
        <Route path="/jobs/:jobId/studio" element={<ResumeStudio />} />
      </Routes>
    </MemoryRouter>,
  );
}

let calls;

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  calls = [];
  globalThis.fetch = vi.fn(async (url, opts = {}) => {
    calls.push({ url: String(url), method: opts.method || 'GET', body: opts.body });
    const u = String(url);
    if (u.endsWith('/sections') && opts.method === 'POST') {
      // The real 202 envelope (app.py:2178).
      return jsonRes({ task_id: 't1', poll_url: '/api/tasks/t1' });
    }
    if (u.includes('/api/tasks/')) {
      // The real task row (_save_task) wrapping _do_rebuild_sections' return.
      return jsonRes({
        status: 'done',
        result: {
          job_id: 'j1',
          tex_s3_key: 'users/u/resumes/j1_tailored.tex',
          pdf_s3_key: 'users/u/resumes/j1_tailored.pdf',
          pdf_url: 'https://s3/after.pdf',
          scores: {
            ats_score: 90, hiring_manager_score: 88, tech_recruiter_score: 92,
            match_score: 90.0, score_spread: { ats: [88, 92], match: [89, 91], n: 3 },
          },
        },
      });
    }
    if (u.endsWith('/sections')) return jsonRes({ sections: SECTIONS, jd_analysis: {} });
    return jsonRes(JOB);
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('compile against the real apiCall contract', () => {
  it('shows the new PDF after an edit, and never requests an undefined URL', async () => {
    renderStudio();
    await waitFor(() => expect(screen.getByLabelText(/summary/i)).toBeInTheDocument());

    const summary = screen.getByLabelText(/summary/i);
    fireEvent.change(summary, { target: { value: 'Edited summary.' } });
    fireEvent.blur(summary);

    await waitFor(
      () => expect(screen.getByTitle(/resume preview/i)).toHaveAttribute('src', 'https://s3/after.pdf'),
      { timeout: 10000 },
    );

    // The bug this file exists for: pollPipeline(undefined) fetched
    // `<API_BASE>undefined` and 404'd on every single compile.
    expect(calls.some((c) => c.url.includes('undefined'))).toBe(false);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('round-trips sections the editor does not render', async () => {
    // projects/education/certifications/header have no UI, but the POST body
    // replaces the whole document server-side — dropping them would silently
    // delete those sections from the resume.
    renderStudio();
    await waitFor(() => expect(screen.getByLabelText(/summary/i)).toBeInTheDocument());

    const summary = screen.getByLabelText(/summary/i);
    fireEvent.change(summary, { target: { value: 'Edited.' } });
    fireEvent.blur(summary);

    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true));
    const posted = JSON.parse(calls.find((c) => c.method === 'POST').body).sections;
    expect(posted.summary).toBe('Edited.');
    expect(posted.projects).toEqual(SECTIONS.projects);
    expect(posted.education).toEqual(SECTIONS.education);
    expect(posted.header).toEqual(SECTIONS.header);
  });
});

describe('never destroy a resume with an empty rebuild', () => {
  it('does not POST when sections failed to load', async () => {
    // A tailored job whose sections GET fails transiently (app.py:2121 returns
    // 500 on any non-NoSuchKey S3 error). sections stays null. Recompile must
    // not send {} — the server would rebuild the .tex from nothing and
    // overwrite both S3 objects with an empty resume.
    globalThis.fetch = vi.fn(async (url, opts = {}) => {
      calls.push({ url: String(url), method: opts.method || 'GET', body: opts.body });
      const u = String(url);
      if (u.endsWith('/sections') && opts.method !== 'POST') {
        return { ok: false, status: 500, json: async () => ({ detail: 'Could not retrieve .tex' }) };
      }
      if (u.endsWith('/sections')) return jsonRes({ task_id: 't1', poll_url: '/api/tasks/t1' });
      return jsonRes(JOB);
    });

    renderStudio();
    await waitFor(() => expect(screen.getByText(/no tailored resume/i)).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /recompile/i }));
    // A real wait, not a microtask: apiCall awaits authHeaders() before it
    // fetches, so `await Promise.resolve()` would let this assertion pass
    // against a broken implementation that POSTs a moment later.
    await new Promise((r) => setTimeout(r, 300));

    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(0);
  });
});

describe('scores never claim to describe an edited document', () => {
  it('goes stale when the compile could not score the new document', async () => {
    // Phase 2 normally re-scores on every compile, so the strip is fresh. But
    // _score_rebuilt_resume returns None on any failure — no description, a
    // Supabase blip, every scoring call exhausted — and the compile still
    // succeeds. In that case the only scores available are the stored row's,
    // which describe the resume BEFORE the edit. Presenting those as a verdict
    // on the new document is exactly what this guard exists to prevent.
    globalThis.fetch = vi.fn(async (url, opts = {}) => {
      calls.push({ url: String(url), method: opts.method || 'GET', body: opts.body });
      const u = String(url);
      if (u.endsWith('/sections') && opts.method === 'POST') {
        return jsonRes({ task_id: 't1', poll_url: '/api/tasks/t1' });
      }
      if (u.includes('/api/tasks/')) {
        return jsonRes({
          status: 'done',
          result: { job_id: 'j1', pdf_url: 'https://s3/after.pdf', scores: null },
        });
      }
      if (u.endsWith('/sections')) return jsonRes({ sections: SECTIONS, jd_analysis: {} });
      return jsonRes(JOB);
    });

    renderStudio();
    await waitFor(() => expect(screen.getByLabelText(/summary/i)).toBeInTheDocument());
    expect(screen.getByTestId('score-strip')).toHaveAttribute('data-stale', 'false');

    const summary = screen.getByLabelText(/summary/i);
    fireEvent.change(summary, { target: { value: 'Edited summary.' } });
    fireEvent.blur(summary);

    await waitFor(
      () => expect(screen.getByTitle(/resume preview/i)).toHaveAttribute('src', 'https://s3/after.pdf'),
      { timeout: 10000 },
    );

    // PDF is current; the scores are not, and the strip says so.
    expect(screen.getByTestId('score-strip')).toHaveAttribute('data-stale', 'true');
    expect(screen.getByTestId('score-strip')).toHaveTextContent(/before your edits/i);
  });
});

describe('the score updates from the compile', () => {
  it('shows the freshly measured band and stops being stale', async () => {
    renderStudio();
    await waitFor(() => expect(screen.getByLabelText(/summary/i)).toBeInTheDocument());
    // Before the edit: the band comes from the stored row's three perspectives.
    expect(screen.getByTestId('score-band')).toHaveTextContent('84–90');

    const summary = screen.getByLabelText(/summary/i);
    fireEvent.change(summary, { target: { value: 'Edited summary.' } });
    fireEvent.blur(summary);

    // After: the band is min/max across three repeat calls on the NEW document.
    await waitFor(
      () => expect(screen.getByTestId('score-band')).toHaveTextContent('89–91'),
      { timeout: 10000 },
    );
    expect(screen.getByTestId('score-strip')).toHaveTextContent(/3 calls/i);
    // Freshly scored against what is on screen, so no longer stale.
    expect(screen.getByTestId('score-strip')).toHaveAttribute('data-stale', 'false');
  });
});
