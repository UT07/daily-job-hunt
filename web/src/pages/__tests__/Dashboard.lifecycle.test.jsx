/**
 * Dashboard — age-based job lifecycle integration.
 *
 * Kept in its own file (rather than folded into Dashboard.test.jsx) so the
 * lifecycle contract is readable on its own and doesn't collide with the
 * in-flight frontend-coverage work.
 *
 * The owner's rule, 2026-09-28:
 *   < 14 days   active   — the working dashboard
 *   14-30 days  stale    — the Past / Outdated shelf
 *   >= 30 days  archived — off the dashboard
 *   applied     exempt   — stays forever, at any age
 *
 * Two things here are verified against prod rather than assumed, because both
 * had already gone wrong once:
 *
 * 1. The active list sends NO `lifecycle` param and relies on the backend's
 *    `not_archived` default. That default used to sit inside a `if filters:`
 *    block in db_client.get_jobs, so an unfiltered call quietly returned all
 *    1,251 rows including the 1,164 archived ones.
 * 2. The applied-jobs exemption. All 38 engaged rows in prod
 *    (Applied/Withdrawn/Rejected) carry `is_expired: true`, so the default
 *    `hide_expired` filter deleted every one of them from the dashboard even
 *    though the age rule had correctly let them through. The fixtures below
 *    use that exact shape — expired, 172 days old, Applied — so the test
 *    fails if that regresses.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const { apiGet, apiPatch, apiDelete } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPatch: vi.fn(),
  apiDelete: vi.fn(),
}));
vi.mock('../../api', () => ({ apiGet, apiPatch, apiDelete }));

vi.mock('../../auth/useAuth', () => ({ useAuth: vi.fn() }));
import { useAuth } from '../../auth/useAuth';

// PipelineStatus polls its own endpoints and has nothing to do with the
// lifecycle contract under test.
vi.mock('../../components/PipelineStatus', () => ({
  default: () => <div data-testid="pipeline-status-stub" />,
}));

import Dashboard from '../Dashboard';

const NOW = Date.now();
const daysAgo = (n) => new Date(NOW - n * 86400000).toISOString();

function makeJob(overrides = {}) {
  return {
    job_id: 'job-1',
    title: 'Senior Backend Engineer',
    company: 'Acme Corp',
    location: 'Dublin, Ireland',
    source: 'linkedin',
    match_score: 82,
    score_tier: 'A',
    application_status: 'New',
    is_expired: false,
    apply_url: 'https://example.com/apply/1',
    first_seen: daysAgo(3),
    posted_date: null,
    resume_s3_url: null,
    cover_letter_s3_url: null,
    resume_doc_url: null,
    tailoring_model: null,
    matched_resume: '',
    linkedin_contacts: null,
    key_matches: [],
    description: '',
    ...overrides,
  };
}

/**
 * Routes every apiGet the page can make. Throws on anything unexpected so a
 * new fetch surfaces as a clear error rather than a hung pending promise.
 */
function installApiGetRouter({ jobs = [], total, staleJobs = [], staleTotal, grandTotal = 0 } = {}) {
  apiGet.mockImplementation((endpoint) => {
    if (endpoint === '/api/dashboard/jobs?page=1&per_page=1') {
      return Promise.resolve({ jobs: [], total: grandTotal });
    }
    if (endpoint.includes('lifecycle=stale')) {
      return Promise.resolve({ jobs: staleJobs, total: staleTotal ?? staleJobs.length });
    }
    if (endpoint.startsWith('/api/dashboard/jobs')) {
      return Promise.resolve({ jobs, total: total ?? jobs.length });
    }
    if (endpoint === '/api/dashboard/stats') {
      return Promise.resolve({
        total_jobs: grandTotal, matched_jobs: jobs.length,
        avg_match_score: 70, jobs_by_status: {},
      });
    }
    if (endpoint === '/api/dashboard/skills') return Promise.resolve({ skills: [] });
    return Promise.reject(new Error(`Unhandled apiGet endpoint in test: ${endpoint}`));
  });
}

// Real active-list fetches carry per_page=25; the grand-total probe carries
// per_page=1; the stale shelf carries lifecycle=stale.
const activeListCalls = () =>
  apiGet.mock.calls.map(([u]) => u).filter((u) => u.includes('per_page=25') && !u.includes('lifecycle='));
const staleCalls = () =>
  apiGet.mock.calls.map(([u]) => u).filter((u) => u.includes('lifecycle=stale'));

function renderDashboard() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Dashboard />
    </MemoryRouter>
  );
}

// JobTable renders a mobile card-stack AND a desktop table at once (Tailwind
// hides one via CSS; jsdom doesn't evaluate media queries), so per-job text
// appears twice in list view.
const expectTextPresent = (t) => expect(screen.getAllByText(t).length).toBeGreaterThan(0);

const pastHeader = () => screen.queryByRole('button', { name: /Past \/ Outdated/ });

beforeEach(() => {
  apiGet.mockReset();
  apiPatch.mockReset().mockResolvedValue({});
  apiDelete.mockReset().mockResolvedValue({});
  useAuth.mockReturnValue({ user: { id: 'u1', email: 'a@b.com' }, loading: false });
  localStorage.clear();
});

describe('Dashboard — the active list keeps the archived-hidden default', () => {
  it('sends no lifecycle param, so the backend default (not_archived) applies', async () => {
    installApiGetRouter({ jobs: [makeJob()], total: 1, grandTotal: 1 });
    renderDashboard();

    await waitFor(() => expect(activeListCalls().length).toBeGreaterThan(0));
    for (const url of activeListCalls()) {
      expect(url).not.toContain('lifecycle');
    }
  });

  it('never asks for lifecycle=all or lifecycle=archived from the main list', async () => {
    installApiGetRouter({ jobs: [makeJob()], total: 1, grandTotal: 1 });
    renderDashboard();

    await waitFor(() => expect(activeListCalls().length).toBeGreaterThan(0));
    const everyUrl = apiGet.mock.calls.map(([u]) => u).join(' ');
    expect(everyUrl).not.toContain('lifecycle=all');
    expect(everyUrl).not.toContain('lifecycle=archived');
  });

  it('still sends the default score floor and expiry filter alongside it', async () => {
    installApiGetRouter({ jobs: [makeJob()], total: 1, grandTotal: 1 });
    renderDashboard();

    await waitFor(() => expect(activeListCalls().length).toBeGreaterThan(0));
    expect(activeListCalls()[0]).toContain('min_score=60');
    expect(activeListCalls()[0]).toContain('hide_expired=true');
  });
});

describe('Dashboard — applied jobs survive at any age', () => {
  // The exact prod shape: applied 172 days ago, posting has since 404'd.
  const appliedAncient = makeJob({
    job_id: 'applied-1',
    title: 'Junior Cloud DevOps Engineer',
    application_status: 'Applied',
    is_expired: true,
    first_seen: daysAgo(172),
  });

  it('renders a 172-day-old Applied job in the main list', async () => {
    installApiGetRouter({ jobs: [appliedAncient], total: 1, grandTotal: 1 });
    renderDashboard();

    await waitFor(() => expectTextPresent('Junior Cloud DevOps Engineer'));
  });

  it('renders ancient Withdrawn and Rejected jobs too — applying is what exempts them', async () => {
    installApiGetRouter({
      jobs: [
        makeJob({ job_id: 'w1', title: 'Staff SRE', application_status: 'Withdrawn', is_expired: true, first_seen: daysAgo(186) }),
        makeJob({ job_id: 'r1', title: 'Graduate AI Engineer', application_status: 'Rejected', is_expired: true, first_seen: daysAgo(150) }),
      ],
      total: 2,
      grandTotal: 2,
    });
    renderDashboard();

    await waitFor(() => expectTextPresent('Staff SRE'));
    expectTextPresent('Graduate AI Engineer');
  });

  it('does not push applied jobs into the Past / Outdated shelf', async () => {
    // The backend excludes engaged rows from lifecycle=stale; the shelf is
    // therefore empty here and must not render at all.
    installApiGetRouter({ jobs: [appliedAncient], total: 1, staleJobs: [], staleTotal: 0, grandTotal: 1 });
    renderDashboard();

    await waitFor(() => expectTextPresent('Junior Cloud DevOps Engineer'));
    await waitFor(() => expect(staleCalls().length).toBeGreaterThan(0));
    expect(pastHeader()).toBeNull();
  });
});

describe('Dashboard — the Past / Outdated shelf', () => {
  const staleJob = makeJob({
    job_id: 'stale-1',
    title: 'Backend Software Engineer',
    company: 'TREQS',
    score_tier: 'B',
    first_seen: daysAgo(27),
  });

  it('appears below the active list when the stale bucket is non-empty', async () => {
    installApiGetRouter({
      jobs: [makeJob({ title: 'Platform Engineer' })], total: 1,
      staleJobs: [staleJob], staleTotal: 39, grandTotal: 40,
    });
    renderDashboard();

    await waitFor(() => expect(pastHeader()).toBeInTheDocument());
    expect(screen.getByText('(39)')).toBeInTheDocument();
  });

  it('does not leak stale jobs into the active list', async () => {
    installApiGetRouter({
      jobs: [makeJob({ title: 'Platform Engineer' })], total: 1,
      staleJobs: [staleJob], staleTotal: 1, grandTotal: 2,
    });
    renderDashboard();

    await waitFor(() => expect(pastHeader()).toBeInTheDocument());
    // Collapsed by default, so the stale job's title is nowhere on the page —
    // in particular it is not in the "1 job" active list.
    expect(screen.queryByText('Backend Software Engineer')).toBeNull();
    expect(screen.getByText('1 job')).toBeInTheDocument();
  });

  it('is absent entirely when nothing is in the 14-30 day band', async () => {
    installApiGetRouter({ jobs: [makeJob()], total: 1, staleJobs: [], staleTotal: 0, grandTotal: 1 });
    renderDashboard();

    await waitFor(() => expect(staleCalls().length).toBeGreaterThan(0));
    expect(pastHeader()).toBeNull();
  });

  it('fetches the stale band exactly once on mount', async () => {
    // The shelf used to be wrapped in `{!loading && ...}`, which unmounted and
    // remounted it around every active-list refetch — two requests per load,
    // and the shelf silently re-collapsed whenever a filter changed.
    installApiGetRouter({
      jobs: [makeJob()], total: 1, staleJobs: [staleJob], staleTotal: 1, grandTotal: 1,
    });
    renderDashboard();

    await waitFor(() => expect(pastHeader()).toBeInTheDocument());
    expect(staleCalls()).toHaveLength(1);
  });

  it('stays expanded across an active-list refetch', async () => {
    installApiGetRouter({
      jobs: [makeJob()], total: 1, staleJobs: [staleJob], staleTotal: 1, grandTotal: 1,
    });
    renderDashboard();

    await waitFor(() => expect(pastHeader()).toBeInTheDocument());
    fireEvent.click(pastHeader());
    expect(pastHeader()).toHaveAttribute('aria-expanded', 'true');

    // Re-sort: the active list refetches, the shelf must not reset.
    fireEvent.change(screen.getByDisplayValue('Seen (newest)'), {
      target: { value: 'match_score:desc' },
    });

    await waitFor(() => expect(activeListCalls().length).toBeGreaterThan(1));
    expect(pastHeader()).toHaveAttribute('aria-expanded', 'true');
  });

  it('asks for the stale band with the same filters as the active list', async () => {
    installApiGetRouter({
      jobs: [makeJob()], total: 1, staleJobs: [staleJob], staleTotal: 1, grandTotal: 1,
    });
    renderDashboard();

    await waitFor(() => expect(staleCalls().length).toBeGreaterThan(0));
    const stale = new URLSearchParams(staleCalls()[0].split('?')[1]);
    expect(stale.get('lifecycle')).toBe('stale');
    stale.delete('lifecycle');
    const active = new URLSearchParams(activeListCalls()[0].split('?')[1]);
    expect(stale.toString()).toBe(active.toString());
  });
});
