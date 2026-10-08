/**
 * Dashboard — out-of-order list responses (shares fixtures with Dashboard.counts).
 *
 * Hidden count. The list shows lifecycle=active (< 14 days, engaged exempt).
 * The number it was subtracted from was NOT that population:
 *   - Hide Expired on: /api/dashboard/stats total_jobs, every non-expired row
 *     of ANY age (db_client.get_job_stats has no lifecycle filter), so the
 *     14-30 day stale band (already shown in Past / Outdated) and every 30+
 *     day archived row read as "hidden by filters";
 *   - otherwise: /api/dashboard/jobs?page=1&per_page=1, the backend default
 *     lifecycle not_archived (< 30 days), so the stale band again.
 * Neither can be cleared by any filter. The baseline must be the same
 * lifecycle with every clearable filter off.
 *
 * Ordering. fetchJobs had no request guard, so a slow earlier response could
 * land after a newer one and overwrite it.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const { apiGet, apiPatch, apiDelete } = vi.hoisted(() => ({
  apiGet: vi.fn(), apiPatch: vi.fn(), apiDelete: vi.fn(),
}));
vi.mock('../../api', () => ({ apiGet, apiPatch, apiDelete }));
vi.mock('../../auth/useAuth', () => ({ useAuth: vi.fn() }));
import { useAuth } from '../../auth/useAuth';
vi.mock('../../components/PipelineStatus', () => ({ default: () => null }));

import Dashboard from '../Dashboard';

function job(id, title) {
  return {
    job_id: id, title, company: 'Acme', location: 'Dublin', source: 'linkedin',
    match_score: 80, score_tier: 'A', application_status: 'New', is_expired: false,
    apply_url: 'https://x.test', first_seen: new Date().toISOString(), key_matches: [],
  };
}

const isBaselineProbe = (u) => u.includes('per_page=1&') || u.endsWith('per_page=1');

/**
 * Populations, mirroring prod's shape: 5 jobs pass the default filters, 8
 * active jobs exist with no filters, 30 are < 30 days (adds the stale band),
 * and stats counts 40 non-expired of any age.
 */
function installRouter({ listTotal = 5, activeUnfiltered = 8, notArchived = 30, statsTotal = 40 } = {}) {
  apiGet.mockImplementation((u) => {
    if (u === '/api/dashboard/stats') {
      return Promise.resolve({ total_jobs: statsTotal, matched_jobs: 1, avg_match_score: 70, jobs_by_status: {} });
    }
    if (u === '/api/dashboard/skills') return Promise.resolve({ skills: [] });
    if (u.includes('lifecycle=stale')) return Promise.resolve({ jobs: [], total: 22 });
    if (u.startsWith('/api/dashboard/jobs') && isBaselineProbe(u)) {
      return Promise.resolve({ jobs: [], total: u.includes('lifecycle=active') ? activeUnfiltered : notArchived });
    }
    if (u.startsWith('/api/dashboard/jobs')) {
      return Promise.resolve({ jobs: Array.from({ length: Math.min(listTotal, 25) }, (_, i) => job(`j${i}`, `Job ${i}`)), total: listTotal });
    }
    return Promise.reject(new Error(`unhandled ${u}`));
  });
}

function renderAt(url = '/') {
  return render(<MemoryRouter initialEntries={[url]}><Dashboard /></MemoryRouter>);
}

beforeEach(() => {
  apiGet.mockReset();
  useAuth.mockReturnValue({ user: { id: 'u1', email: 'a@b.com' }, loading: false });
  localStorage.clear();
});

describe('out-of-order list responses', () => {
  it('a slow earlier response cannot overwrite a newer one', async () => {
    installRouter({ listTotal: 2 });
    renderAt('/?min_score=0&hide_expired=false');
    await screen.findByText('2 jobs');

    // Hold the next two list fetches open, then resolve them in reverse.
    const pending = [];
    const base = apiGet.getMockImplementation();
    apiGet.mockImplementation((u) => {
      if (u.startsWith('/api/dashboard/jobs') && u.includes('per_page=25') && u.includes('lifecycle=active')) {
        return new Promise((resolve) => pending.push({ u, resolve }));
      }
      return base(u);
    });

    const box = screen.getByLabelText('Search jobs by title or company');
    fireEvent.change(box, { target: { value: 'stripe' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    await waitFor(() => expect(pending.length).toBe(1));
    fireEvent.change(box, { target: { value: '' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    await waitFor(() => expect(pending.length).toBe(2));
    expect(pending[0].u).toContain('q=stripe');
    expect(pending[1].u).not.toContain('q=stripe');

    // Newer request (no search) answers first...
    await act(async () => {
      pending[1].resolve({ jobs: [job('n1', 'Newest A'), job('n2', 'Newest B'), job('n3', 'Newest C')], total: 3 });
    });
    await screen.findByText('3 jobs');
    // ...then the stale "stripe" search lands late.
    await act(async () => {
      pending[0].resolve({ jobs: [job('s1', 'Stripe SRE')], total: 1 });
    });

    expect(screen.getByText('3 jobs')).toBeInTheDocument();
    expect(screen.queryByText('Stripe SRE')).toBeNull();
  });
});
