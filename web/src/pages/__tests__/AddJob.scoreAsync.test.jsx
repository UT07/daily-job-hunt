/**
 * Save & Score against the REAL apiCall, with only `fetch` stubbed.
 *
 * A fresh POST /api/score used to score inside the request: three sequential
 * LLM calls (median of three, 2026-10-08) took 72.6s in production, API
 * Gateway gives up at ~30s, and the user got a 503 for a job that was in fact
 * scored and saved. It now answers 202 {task_id, poll_url}; apiCall follows
 * the poll URL and resolves with the task's `result`.
 *
 * The other AddJob tests mock api.js wholesale, so none of them could see
 * whether the card renders a POLLED result. This one lets apiCall/pollTask
 * run, and the result is `score_task_result.json` -- the fixture
 * tests/unit/test_score_is_async.py checks against what the real SQS worker
 * stores, so the shape here is the shape production produces.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import SCORE_RESULT from '../../test/fixtures/score_task_result.json';

vi.mock('../../auth/useAuth', () => ({
  useAuth: () => ({ user: { id: 'u1', email: 'a@b.com' }, loading: false }),
}));

import AddJob from '../AddJob';

const JD = 'We are hiring a Site Reliability Engineer to own our Kubernetes platform.';

function jsonRes(body, status = 200) {
  return { ok: status < 400, status, json: async () => body };
}

let calls;

function stubFetch({ scoreStatus, scoreBody, polls }) {
  const queue = [...(polls || [])];
  globalThis.fetch = vi.fn(async (url, opts = {}) => {
    const u = String(url);
    calls.push({ url: u, method: opts.method || 'GET' });
    if (u.endsWith('/api/score')) return jsonRes(scoreBody, scoreStatus);
    if (u.includes('/api/tasks/')) return jsonRes(queue.length > 1 ? queue.shift() : queue[0]);
    return jsonRes({});
  });
}

function renderAndScore() {
  render(<MemoryRouter><AddJob /></MemoryRouter>);
  fireEvent.change(screen.getByLabelText('Job Description'), { target: { value: JD } });
  fireEvent.click(screen.getByRole('button', { name: /save & score/i }));
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  sessionStorage.clear();
  calls = [];
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('Save & Score, fresh path (202 then poll)', () => {
  it('follows the poll URL and renders the polled ScoreResponse', async () => {
    stubFetch({
      scoreStatus: 202,
      scoreBody: { task_id: 't-score', poll_url: '/api/tasks/t-score' },
      polls: [{ status: 'running' }, { status: 'done', result: SCORE_RESULT }],
    });

    renderAndScore();

    await waitFor(
      () => expect(screen.getByText(SCORE_RESULT.reasoning)).toBeInTheDocument(),
      { timeout: 10000 },
    );
    expect(screen.getByText(/Score Card/)).toBeInTheDocument();
    expect(screen.getByText(`Resume: ${SCORE_RESULT.matched_resume}`)).toBeInTheDocument();
    expect(screen.getByText('Saved to your dashboard.')).toBeInTheDocument();
    // A fresh score is not presented as a stored one.
    expect(screen.queryByText(/Already scored/)).not.toBeInTheDocument();

    const polled = calls.filter((c) => c.url.includes('/api/tasks/t-score'));
    expect(polled.length).toBeGreaterThanOrEqual(2);
    expect(calls.some((c) => c.url.includes('undefined'))).toBe(false);
  });

  it('says "not saved" when the task scored but could not write the row', async () => {
    stubFetch({
      scoreStatus: 202,
      scoreBody: { task_id: 't-score', poll_url: '/api/tasks/t-score' },
      polls: [{ status: 'done', result: { ...SCORE_RESULT, job_id: null, saved: false } }],
    });

    renderAndScore();

    await waitFor(
      () => expect(screen.getByRole('status')).toHaveTextContent(/not saved/i),
      { timeout: 10000 },
    );
  });

  it('shows the task error instead of a card when scoring failed on the worker', async () => {
    stubFetch({
      scoreStatus: 202,
      scoreBody: { task_id: 't-score', poll_url: '/api/tasks/t-score' },
      polls: [{ status: 'error', error: '500: AI scoring failed: every provider refused' }],
    });

    renderAndScore();

    await waitFor(
      () => expect(screen.getByText(/AI scoring failed/)).toBeInTheDocument(),
      { timeout: 10000 },
    );
    expect(screen.queryByText(/Score Card/)).not.toBeInTheDocument();
  });
});

describe('Save & Score, reuse path (synchronous 200)', () => {
  it('renders a stored score straight from the response, with no poll', async () => {
    stubFetch({ scoreStatus: 200, scoreBody: { ...SCORE_RESULT, reused: true } });

    renderAndScore();

    await waitFor(() => expect(screen.getByText(SCORE_RESULT.reasoning)).toBeInTheDocument());
    expect(screen.getByText(/Already scored/)).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes('/api/tasks/'))).toBe(false);
  });
});
