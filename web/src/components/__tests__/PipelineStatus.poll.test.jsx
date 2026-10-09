/**
 * PipelineStatus polls GET /api/pipeline/status/{execution_name} every 5s
 * after "Run Pipeline". A failing poll was console.error'd and the interval
 * kept running forever: the button said "Running..." indefinitely with no
 * message. Now each failure is shown, and after 3 in a row polling stops.
 *
 * Shapes from app.py: POST /api/pipeline/run returns {executionArn,
 * startDate, pollUrl}; the status endpoint returns {name, status, startDate,
 * stopDate, output} and, for a FAILED run, `error` and `cause`.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';

const { apiGet, apiCall } = vi.hoisted(() => ({ apiGet: vi.fn(), apiCall: vi.fn() }));
vi.mock('../../api', () => ({ apiGet, apiCall }));
import PipelineStatus from '../PipelineStatus';

let pollImpl;

beforeEach(() => {
  vi.useFakeTimers();
  apiGet.mockReset();
  apiCall.mockReset();
  apiGet.mockImplementation((u) => {
    if (u === '/api/pipeline/status') return Promise.resolve({ latest_run: null, today_metrics: [] });
    if (u === '/api/search-config') return Promise.resolve({ queries: ['sre'] });
    if (u.startsWith('/api/pipeline/status/')) return pollImpl(u);
    return Promise.reject(new Error(`unhandled ${u}`));
  });
  apiCall.mockResolvedValue({
    executionArn: 'arn:aws:states:eu-west-1:1:execution:daily:exec-1',
    startDate: '2026-10-08T10:00:00',
    pollUrl: '/api/pipeline/status/exec-1',
  });
});
afterEach(() => vi.useRealTimers());

async function startRun() {
  render(<PipelineStatus />);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  fireEvent.click(screen.getByRole('button', { name: /Run Pipeline/ }));
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
}
const tick = () => act(async () => { await vi.advanceTimersByTimeAsync(5000); });
const pollCalls = () => apiGet.mock.calls.filter(([u]) => u.startsWith('/api/pipeline/status/')).length;

describe('PipelineStatus polling', () => {
  it('shows a poll failure instead of only logging it', async () => {
    pollImpl = () => Promise.reject(new Error('Pipeline status check failed: AccessDenied'));
    await startRun();
    await tick();
    expect(screen.getByText(/AccessDenied/)).toBeInTheDocument();
  });

  it('stops polling after 3 consecutive failures', async () => {
    pollImpl = () => Promise.reject(new Error('HTTP 502'));
    await startRun();
    await tick(); await tick(); await tick();
    expect(pollCalls()).toBe(3);
    await tick(); await tick();
    expect(pollCalls()).toBe(3);
    expect(screen.getByText(/stopped checking/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Run Pipeline/ })).not.toBeDisabled();
  });

  it('a success between failures resets the count', async () => {
    let n = 0;
    pollImpl = () => (++n % 3 === 0
      ? Promise.resolve({ name: 'exec-1', status: 'RUNNING', startDate: 'x', output: null })
      : Promise.reject(new Error('HTTP 502')));
    await startRun();
    for (let i = 0; i < 6; i++) await tick();
    expect(pollCalls()).toBe(6);
  });

  it('shows error and cause for a FAILED execution', async () => {
    pollImpl = () => Promise.resolve({
      name: 'exec-1', status: 'FAILED', startDate: 'x', stopDate: 'y', output: null,
      error: 'States.TaskFailed', cause: 'ScoreBatch timed out',
    });
    await startRun();
    await tick();
    expect(screen.getByText(/States\.TaskFailed: ScoreBatch timed out/)).toBeInTheDocument();
  });
});
