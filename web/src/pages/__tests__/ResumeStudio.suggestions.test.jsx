/**
 * Suggestions inside the Studio, against the REAL apiCall — no api.js stubs.
 *
 * Same discipline as ResumeStudio.contract.test.jsx, and for the same reason:
 * the suggestions endpoint is a 202 + poll, and a mock of `apiCall` would only
 * test this file's belief about that envelope. So `fetch` is stubbed and the
 * real apiCall/pollTask run over it.
 *
 * Two things this pins that nothing else can:
 *
 *   - The sections sent for analysis are the ones in the EDITOR, not the ones
 *     on disk. The Studio compiles on blur, so the editor routinely holds text
 *     the stored .tex does not; analysing the stored version would produce
 *     advice about lines the user has already rewritten.
 *
 *   - Applying a suggestion does not compile. §4 names the compile triggers —
 *     section blur and the Recompile button — and a 15s round trip per
 *     accepted suggestion would punish the user who accepts four in a row. The
 *     document is still visibly uncompiled afterwards, which is the property
 *     that matters.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ResumeStudio from '../ResumeStudio';

const BULLET = 'Developed React frontends for internal ops dashboards';
const BETTER = 'Shipped React ops dashboards used by 400 staff daily';

const JOB = {
  job_id: 'j1', title: 'Platform Engineer', company: 'Acme',
  ats_score: 86, hiring_manager_score: 84, tech_recruiter_score: 90,
  key_matches: [], gaps: [], requirement_map: [],
  resume_s3_url: 'https://s3/before.pdf',
};

const SECTIONS = {
  header: { name: 'Jane', title: 'SRE', contact: 'j@x.com' },
  summary: 'Platform engineer.',
  skills: [{ category: 'Cloud', items: 'AWS' }],
  experience: [{ company: 'Clover IT Services', title: 'Engineer', bullets: [BULLET] }],
  projects: [], education: [], certifications: [],
};

const SUGGESTION = {
  id: 's2',
  path: ['experience', 0, 'bullets', 0],
  label: 'Experience · Clover IT Services',
  anchor_text: BULLET,
  replacement: BETTER,
  why: 'no quantified impact, and the JD asks for scale',
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
let suggestionsResult;

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  calls = [];
  suggestionsResult = { status: 'done', result: { job_id: 'j1', suggestions: [SUGGESTION] } };
  globalThis.fetch = vi.fn(async (url, opts = {}) => {
    const u = String(url);
    calls.push({ url: u, method: opts.method || 'GET', body: opts.body });
    if (u.endsWith('/suggestions')) return jsonRes({ task_id: 'sug1', poll_url: '/api/tasks/sug1' });
    if (u.includes('/api/tasks/sug1')) return jsonRes(suggestionsResult);
    if (u.endsWith('/sections') && opts.method === 'POST') {
      return jsonRes({ task_id: 'c1', poll_url: '/api/tasks/c1' });
    }
    if (u.includes('/api/tasks/c1')) {
      return jsonRes({ status: 'done', result: { pdf_url: 'https://s3/after.pdf', scores: null } });
    }
    if (u.endsWith('/sections')) return jsonRes({ sections: SECTIONS, jd_analysis: {} });
    return jsonRes(JOB);
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

async function openStudio() {
  renderStudio();
  await waitFor(() => expect(screen.getByLabelText(/summary/i)).toBeInTheDocument());
}

const POLL = { timeout: 10000 };

const askForSuggestions = () =>
  fireEvent.click(screen.getByRole('button', { name: /suggest improvements/i }));

describe('asking for suggestions', () => {
  it('sends the sections in the editor, including edits not yet compiled', async () => {
    await openStudio();

    const bullet = screen.getByLabelText(/Clover IT Services bullet 1/i);
    fireEvent.change(bullet, { target: { value: 'An uncompiled edit' } });
    askForSuggestions();

    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/suggestions'))).toBe(true));
    const post = calls.find((c) => c.url.endsWith('/suggestions'));
    expect(post.method).toBe('POST');
    expect(JSON.parse(post.body).sections.experience[0].bullets[0]).toBe('An uncompiled edit');
  });

  it('renders what comes back, following the 202 to the task result', async () => {
    await openStudio();
    askForSuggestions();
    // pollTask waits 2s before its first poll, so this needs more than the
    // 1s waitFor default — same reason the contract tests raise it.
    await waitFor(() => expect(screen.getByTestId('suggestion-s2')).toBeInTheDocument(), POLL);
    expect(screen.getByTestId('suggestion-s2')).toHaveTextContent('no quantified impact');
  });

  it('shows a failure rather than an empty panel', async () => {
    suggestionsResult = { status: 'error', error: 'All AI providers failed' };
    await openStudio();
    askForSuggestions();
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/All AI providers failed/), POLL);
  });
});

describe('applying a suggestion', () => {
  async function applyOne() {
    await openStudio();
    askForSuggestions();
    await waitFor(() => expect(screen.getByTestId('suggestion-s2')).toBeInTheDocument(), POLL);
    fireEvent.click(screen.getByRole('button', { name: /^apply$/i }));
  }

  it('rewrites the field in the editor', async () => {
    await applyOne();
    await waitFor(() =>
      expect(screen.getByLabelText(/Clover IT Services bullet 1/i)).toHaveValue(BETTER));
  });

  it('leaves the document visibly uncompiled instead of compiling on the spot', async () => {
    await applyOne();
    await waitFor(() => expect(screen.getByText(/1 change not yet compiled/i)).toBeInTheDocument());
    expect(calls.filter((c) => c.url.endsWith('/sections') && c.method === 'POST')).toHaveLength(0);
  });

  it('greys the score, because it now describes a document that no longer exists', async () => {
    await applyOne();
    await waitFor(() =>
      expect(screen.getByTestId('score-strip')).toHaveAttribute('data-stale', 'true'));
  });

  it('is undone locally, with no second call to the model', async () => {
    await applyOne();
    const before = calls.length;
    fireEvent.click(screen.getByRole('button', { name: /undo/i }));
    await waitFor(() =>
      expect(screen.getByLabelText(/Clover IT Services bullet 1/i)).toHaveValue(BULLET));
    expect(calls.length).toBe(before);
  });
});
