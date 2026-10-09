/**
 * "Run Pipeline" with no search queries configured.
 *
 * The component's own comment said the button is disabled with a "configure
 * search first" hint, but `disabled={running}` was all it checked: a click
 * POSTed `{queries: []}` to /api/pipeline/run. PipelineRunRequest's default
 * (["software engineer"]) only applies when the key is ABSENT, so the empty
 * list went straight into the Step Functions input and a run started that
 * could scrape nothing.
 *
 * Shapes from app.py: GET /api/search-config returns `{queries: [...], ...}`.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const { apiGet, apiCall } = vi.hoisted(() => ({ apiGet: vi.fn(), apiCall: vi.fn() }));
vi.mock('../../api', () => ({ apiGet, apiCall }));
import PipelineStatus from '../PipelineStatus';

function mockConfig(searchConfig) {
  apiGet.mockImplementation((u) => {
    if (u === '/api/pipeline/status') return Promise.resolve({ latest_run: null, today_metrics: [] });
    if (u === '/api/search-config') return searchConfig();
    return Promise.reject(new Error(`unhandled ${u}`));
  });
}

beforeEach(() => {
  apiGet.mockReset();
  apiCall.mockReset();
  apiCall.mockResolvedValue({ pollUrl: '/api/pipeline/status/exec-1' });
});

describe('Run Pipeline without search queries', () => {
  it('is disabled, shows the hint, and sends nothing when no queries are configured', async () => {
    mockConfig(() => Promise.resolve({ queries: [] }));
    render(<PipelineStatus />);

    expect(await screen.findByText(/No search queries configured/)).toBeInTheDocument();
    const btn = screen.getByRole('button', { name: /Run Pipeline/ });
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(apiCall).not.toHaveBeenCalled();
  });

  it('is disabled when the search config could not be loaded', async () => {
    mockConfig(() => Promise.reject(new Error('HTTP 503')));
    render(<PipelineStatus />);

    expect(await screen.findByText(/Could not load your search queries/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Run Pipeline/ })).toBeDisabled();
  });

  it('runs with the configured queries', async () => {
    mockConfig(() => Promise.resolve({ queries: ['sre', ''] }));
    render(<PipelineStatus />);

    const btn = await screen.findByRole('button', { name: /Run Pipeline/ });
    await waitFor(() => expect(btn).not.toBeDisabled());
    fireEvent.click(btn);
    await waitFor(() => expect(apiCall).toHaveBeenCalledWith('/api/pipeline/run', { queries: ['sre'] }));
  });

  it('is disabled while the search config is still loading', async () => {
    // Status has rendered the button; the config request has not answered.
    mockConfig(() => new Promise(() => {}));
    render(<PipelineStatus />);

    const btn = await screen.findByRole('button', { name: /Run Pipeline/ });
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(apiCall).not.toHaveBeenCalled();
  });
});
