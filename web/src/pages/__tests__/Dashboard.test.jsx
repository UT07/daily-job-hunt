/**
 * Dashboard — the main job list page. Covers the user-visible core called
 * out in the coverage brief, all traceable to audit-dashboard.md:
 *
 * - P0-1: the default view previously showed ~1 job out of 1,243 with no
 *   explanation. This pins the "N hidden by filters" affordance and the
 *   three distinct empty states (no jobs ever / filtered-to-zero / a
 *   populated list) so they can't be silently reconflated again.
 * - P1-1: CardView silently dropped onStatusChange/onDelete. The view
 *   choice persists in localStorage, so a returning user can land straight
 *   in card mode — status-change and delete are tested in BOTH view modes.
 * - Tier filtering and the sort control are tested as real refetches
 *   (asserting on the outgoing API query string), not just prop-drilling.
 *
 * Ground truth used for fixtures: scores live in `match_score` (never
 * `final_score`, which the live table has null on nearly every row).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const { apiGet, apiPatch, apiDelete } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPatch: vi.fn(),
  apiDelete: vi.fn(),
}));
vi.mock('../../api', () => ({ apiGet, apiPatch, apiDelete }));

vi.mock('../../auth/useAuth', () => ({ useAuth: vi.fn() }));
import { useAuth } from '../../auth/useAuth';

// PipelineStatus polls its own endpoints and isn't part of this page's
// job-list/filter/empty-state contract (no findings from the brief touch
// it) — stubbing it keeps these tests focused and avoids over-mocking
// apiGet with concerns unrelated to what's under test.
vi.mock('../../components/PipelineStatus', () => ({
  default: () => <div data-testid="pipeline-status-stub" />,
}));

import Dashboard from '../Dashboard';

function makeJob(overrides = {}) {
  return {
    job_id: 'job-1',
    title: 'Senior Backend Engineer',
    company: 'Acme Corp',
    location: 'Dublin, Ireland',
    source: 'linkedin',
    match_score: 82,
    score_tier: 'A',
    ats_score: 80,
    hiring_manager_score: 85,
    tech_recruiter_score: 78,
    application_status: 'New',
    is_expired: false,
    apply_url: 'https://example.com/apply/1',
    first_seen: '2026-09-01T00:00:00Z',
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

// Routes every apiGet call this page (and its real, unmocked children —
// JobTable/CardView/StatsBar) can make during these tests. Deliberately
// throws on anything unexpected so a future new fetch doesn't hang a test
// in a pending promise with a confusing timeout.
function installApiGetRouter({ jobs = [], total, grandTotal = 0, statsTotalJobs = grandTotal } = {}) {
  apiGet.mockImplementation((endpoint) => {
    if (endpoint === '/api/dashboard/jobs?page=1&per_page=1') {
      return Promise.resolve({ jobs: [], total: grandTotal });
    }
    if (endpoint.startsWith('/api/dashboard/jobs')) {
      return Promise.resolve({ jobs, total: total ?? jobs.length });
    }
    if (endpoint === '/api/dashboard/stats') {
      return Promise.resolve({
        total_jobs: statsTotalJobs,
        matched_jobs: jobs.length,
        avg_match_score: 70,
        jobs_by_status: {},
      });
    }
    if (endpoint === '/api/dashboard/skills') {
      return Promise.resolve({ skills: [] });
    }
    return Promise.reject(new Error(`Unhandled apiGet endpoint in test: ${endpoint}`));
  });
}

// Real fetchJobs calls always carry `per_page=25` (the fixed page size);
// the grand-total probe is the one call that carries `per_page=1`. Filtering
// on that distinguishes them without depending on call order.
function jobsFetchCalls() {
  return apiGet.mock.calls.map(([url]) => url).filter((url) => url.includes('per_page=25'));
}

function renderDashboard() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Dashboard />
    </MemoryRouter>
  );
}

// JobTable renders BOTH a mobile card-stack and a desktop table at once
// (Tailwind's `md:hidden` / `hidden md:block` controls which is visible via
// CSS — jsdom doesn't evaluate media queries, so both exist in the DOM).
// Any text that appears once per job — title, company, status label — is
// therefore duplicated in Table view. CardView has no such duplication.
// These helpers paper over that so assertions read as plain user-visible
// checks instead of re-deriving this quirk in every test.
function expectTextPresent(text) {
  expect(screen.getAllByText(text).length).toBeGreaterThan(0);
}
function expectTextGone(text) {
  expect(screen.queryAllByText(text).length).toBe(0);
}
// Status names ("New", "Interview", ...) collide with the Status filter
// bar's <option> elements, which are always in the DOM regardless of view
// mode or job content. The dropdown trigger/options are real <button>s;
// filtering by tag disambiguates without needing a container to scope to.
function getStatusButton(text) {
  return screen.getAllByText(text).find((el) => el.tagName === 'BUTTON');
}

beforeEach(() => {
  apiGet.mockReset();
  apiPatch.mockReset().mockResolvedValue({});
  apiDelete.mockReset().mockResolvedValue({});
  useAuth.mockReturnValue({ user: { id: 'u1', email: 'a@b.com' }, loading: false });
  localStorage.clear();
});

describe('Dashboard — job list rendering', () => {
  it('renders the jobs returned by the API and the total count', async () => {
    installApiGetRouter({
      jobs: [makeJob({ job_id: 'j1', title: 'Platform Engineer' }), makeJob({ job_id: 'j2', title: 'SRE II' })],
      total: 2,
      grandTotal: 2,
      statsTotalJobs: 2,
    });
    renderDashboard();

    await waitFor(() => expectTextPresent('Platform Engineer'));
    expectTextPresent('SRE II');
    expect(screen.getByText('2 jobs')).toBeInTheDocument();
    // Nothing is hidden — baseline equals what's shown.
    expect(screen.queryByText(/hidden by filters/)).toBeNull();
  });
});

describe('Dashboard — "N hidden by filters" (audit P0-1)', () => {
  it('shows how many non-expired jobs are hidden when filters narrow the result set', async () => {
    // hide_expired defaults to true, so the baseline is stats.total_jobs
    // (non-expired count) — mirrors the real 1,251-job corpus where only
    // ~72 are non-expired and a tier/score filter narrows further still.
    installApiGetRouter({
      jobs: [makeJob()],
      total: 1,
      grandTotal: 72,
      statsTotalJobs: 72,
    });
    renderDashboard();

    await waitFor(() => expect(screen.getByText(/1 job\b/)).toBeInTheDocument());
    expect(screen.getByText(/71 more non-expired jobs hidden by filters/)).toBeInTheDocument();
  });

  it('shows no "hidden" message once the shown count matches the baseline', async () => {
    installApiGetRouter({ jobs: [makeJob()], total: 72, grandTotal: 72, statsTotalJobs: 72 });
    renderDashboard();

    await waitFor(() => expect(screen.getByText('72 jobs')).toBeInTheDocument());
    expect(screen.queryByText(/hidden by filters/)).toBeNull();
  });
});

describe('Dashboard — tier filtering', () => {
  it('clicking a tier tab re-fetches with that tier applied', async () => {
    installApiGetRouter({ jobs: [makeJob()], total: 1, grandTotal: 72, statsTotalJobs: 72 });
    renderDashboard();
    await waitFor(() => expect(jobsFetchCalls().length).toBeGreaterThan(0));

    fireEvent.click(screen.getByText('Strong Match'));

    await waitFor(() => {
      const calls = jobsFetchCalls();
      expect(calls[calls.length - 1]).toContain('tier=A');
    });
  });

  it('"All Jobs" does not send a tier filter', async () => {
    installApiGetRouter({ jobs: [makeJob()], total: 1, grandTotal: 72, statsTotalJobs: 72 });
    renderDashboard();
    await waitFor(() => expect(jobsFetchCalls().length).toBeGreaterThan(0));

    fireEvent.click(screen.getByText('Must Apply'));
    await waitFor(() => expect(jobsFetchCalls().some((u) => u.includes('tier=S'))).toBe(true));

    fireEvent.click(screen.getByText('All Jobs'));
    await waitFor(() => {
      const calls = jobsFetchCalls();
      expect(calls[calls.length - 1]).not.toContain('tier=');
    });
  });
});

describe('Dashboard — sort control', () => {
  it('changing the sort dropdown re-fetches with the new sort params', async () => {
    installApiGetRouter({ jobs: [makeJob()], total: 1, grandTotal: 1, statsTotalJobs: 1 });
    renderDashboard();
    await waitFor(() => expect(jobsFetchCalls().length).toBeGreaterThan(0));

    const sortSelect = screen.getByText('Score (highest)').closest('select');
    fireEvent.change(sortSelect, { target: { value: 'title:asc' } });

    await waitFor(() => {
      const calls = jobsFetchCalls();
      const last = calls[calls.length - 1];
      expect(last).toContain('sort_by=title');
      expect(last).toContain('sort_order=asc');
    });
  });
});

describe('Dashboard — empty states (audit P0-1: these are different states)', () => {
  it('shows the "no jobs yet" onboarding state when nothing has ever been added', async () => {
    installApiGetRouter({ jobs: [], total: 0, grandTotal: 0, statsTotalJobs: 0 });
    renderDashboard();

    await waitFor(() =>
      expect(screen.getByText(/No jobs yet\. Add one manually or run the pipeline/)).toBeInTheDocument()
    );
    expect(screen.queryByText(/No jobs match your current filters/)).toBeNull();
  });

  it('shows the "filtered to zero" state (distinct from "no jobs yet") when jobs exist but filters hide all of them', async () => {
    installApiGetRouter({ jobs: [], total: 0, grandTotal: 72, statsTotalJobs: 72 });
    renderDashboard();

    await waitFor(() => expect(screen.getByText(/No jobs match your current filters/)).toBeInTheDocument());
    expect(screen.getByText(/72 non-expired jobs are hidden by the filters below/)).toBeInTheDocument();
    expect(screen.queryByText(/No jobs yet\. Add one manually/)).toBeNull();

    // Clearing filters must actually change the outgoing request, not just
    // the message — hide_expired (on by default) should drop off.
    let calls = jobsFetchCalls();
    expect(calls[calls.length - 1]).toContain('hide_expired=true');

    fireEvent.click(screen.getByText('Clear all filters'));

    await waitFor(() => {
      calls = jobsFetchCalls();
      expect(calls[calls.length - 1]).not.toContain('hide_expired=true');
    });
  });
});

describe('Dashboard — status change and delete in Table view', () => {
  it('changing status via the table dropdown calls the API and updates the row', async () => {
    installApiGetRouter({
      jobs: [makeJob({ job_id: 'job-42', application_status: 'New' })],
      total: 1,
      grandTotal: 1,
      statsTotalJobs: 1,
    });
    const { container } = renderDashboard();
    await waitFor(() => expectTextPresent('Senior Backend Engineer'));
    expect(container.querySelector('table')).not.toBeNull();

    fireEvent.click(getStatusButton('New'));
    fireEvent.click(getStatusButton('Interview'));

    await waitFor(() =>
      expect(apiPatch).toHaveBeenCalledWith('/api/dashboard/jobs/job-42', { application_status: 'Interview' })
    );
    await waitFor(() => expectTextPresent('Interview'));
  });

  it('deleting a job from the table calls the API and removes the row', async () => {
    installApiGetRouter({
      jobs: [makeJob({ job_id: 'job-77' })],
      total: 1,
      grandTotal: 1,
      statsTotalJobs: 1,
    });
    const { container } = renderDashboard();
    await waitFor(() => expectTextPresent('Senior Backend Engineer'));

    const table = container.querySelector('table');
    fireEvent.click(within(table).getAllByTitle('Delete job')[0]);
    fireEvent.click(within(table).getAllByText('Yes')[0]);

    await waitFor(() => expect(apiDelete).toHaveBeenCalledWith('/api/dashboard/jobs/job-77'));
    await waitFor(() => expectTextGone('Senior Backend Engineer'));
  });
});

describe('Dashboard — status change and delete in Card view (audit P1-1 regression)', () => {
  beforeEach(() => {
    // The view choice is a persisted preference — a returning user can land
    // straight in card mode without ever touching the toggle.
    localStorage.setItem('naukribaba_view', 'card');
  });

  it('renders cards (no table) when the persisted preference is "card"', async () => {
    installApiGetRouter({ jobs: [makeJob()], total: 1, grandTotal: 1, statsTotalJobs: 1 });
    const { container } = renderDashboard();
    await waitFor(() => expect(screen.getByText('Senior Backend Engineer')).toBeInTheDocument());
    expect(container.querySelector('table')).toBeNull();
  });

  it('changing status from a card calls the API and updates the badge', async () => {
    installApiGetRouter({
      jobs: [makeJob({ job_id: 'job-42', application_status: 'New' })],
      total: 1,
      grandTotal: 1,
      statsTotalJobs: 1,
    });
    renderDashboard();
    await waitFor(() => expect(screen.getByText('Senior Backend Engineer')).toBeInTheDocument());

    fireEvent.click(getStatusButton('New'));
    fireEvent.click(getStatusButton('Interview'));

    await waitFor(() =>
      expect(apiPatch).toHaveBeenCalledWith('/api/dashboard/jobs/job-42', { application_status: 'Interview' })
    );
    await waitFor(() => expect(getStatusButton('Interview')).toBeInTheDocument());
  });

  it('deleting from a card calls the API and removes the card', async () => {
    installApiGetRouter({
      jobs: [makeJob({ job_id: 'job-77', title: 'Only Card Job' })],
      total: 1,
      grandTotal: 1,
      statsTotalJobs: 1,
    });
    renderDashboard();
    await waitFor(() => expect(screen.getByText('Only Card Job')).toBeInTheDocument());

    fireEvent.click(screen.getByTitle('Delete job'));
    fireEvent.click(screen.getByText('Yes'));

    await waitFor(() => expect(apiDelete).toHaveBeenCalledWith('/api/dashboard/jobs/job-77'));
    await waitFor(() => expect(screen.queryByText('Only Card Job')).toBeNull());
  });

  it('toggling the view control switches between table and card layouts', async () => {
    installApiGetRouter({ jobs: [makeJob()], total: 1, grandTotal: 1, statsTotalJobs: 1 });
    const { container } = renderDashboard();
    await waitFor(() => expect(screen.getByText('Senior Backend Engineer')).toBeInTheDocument());
    expect(container.querySelector('table')).toBeNull(); // starts in card mode (see outer beforeEach)

    fireEvent.click(screen.getByTitle('List view'));
    await waitFor(() => expect(container.querySelector('table')).not.toBeNull());

    fireEvent.click(screen.getByTitle('Card view'));
    await waitFor(() => expect(container.querySelector('table')).toBeNull());
  });
});
