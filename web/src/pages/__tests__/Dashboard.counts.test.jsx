/**
 * Dashboard — "N more jobs hidden by filters" and out-of-order list responses.
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

describe('hidden-by-filters count', () => {
  it('counts only active jobs a clearable filter hides (default filters on)', async () => {
    installRouter();
    renderAt('/');
    // 8 active unfiltered - 5 shown = 3. Not 40 - 5 (stats: any age) nor 30 - 5 (stale band).
    expect(await screen.findByText(/3 more .*hidden by filters/)).toBeInTheDocument();
    expect(screen.queryByText(/35 more|25 more/)).toBeNull();
  });

  it('says nothing when no filter is active', async () => {
    installRouter({ listTotal: 8 });
    renderAt('/?min_score=0&hide_expired=false');
    await screen.findByText('8 jobs');
    // The stale band (30 - 8 = 22) is not "hidden by filters" — no filter is on.
    expect(screen.queryByText(/hidden by filters/)).toBeNull();
  });

  it('says nothing when no filter is active even if the totals disagree', async () => {
    // e.g. a job arrived between the two requests.
    installRouter({ listTotal: 7, activeUnfiltered: 8 });
    renderAt('/?min_score=0&hide_expired=false');
    await screen.findByText('7 jobs');
    expect(screen.queryByText(/hidden by filters/)).toBeNull();
  });
});
