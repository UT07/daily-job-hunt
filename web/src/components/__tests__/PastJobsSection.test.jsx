/**
 * PastJobsSection — the "Past / Outdated" (14-30 day) shelf.
 *
 * Covers the owner's rule as stated 2026-09-28: 14-30 days is past/outdated
 * but still reachable, 30+ is gone, applied is exempt forever. The backend
 * does the bucketing; what these tests pin is that the UI asks for the right
 * bucket and stays out of the way when there is nothing in it.
 *
 * Fixture shape is taken from the live `jobs` table, not invented: rows carry
 * `first_seen` (there is no `created_at` column), `score_tier`, `match_score`,
 * `application_status` and `is_expired`. Ages of 26-28 days are the real
 * contents of the stale bucket in prod today.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const { apiGet, apiDelete } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiDelete: vi.fn(),
}));
vi.mock('../../api', () => ({ apiGet, apiDelete }));

const navigate = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => navigate,
}));

import PastJobsSection from '../PastJobsSection';

const NOW = Date.now();
const daysAgo = (n) => new Date(NOW - n * 86400000).toISOString();

function makeStaleJob(overrides = {}) {
  return {
    job_id: 'stale-1',
    title: 'Backend Software Engineer',
    company: 'TREQS',
    location: 'Dublin, Ireland',
    source: 'linkedin',
    match_score: 72,
    score_tier: 'B',
    application_status: 'New',
    is_expired: false,
    first_seen: daysAgo(27),
    ...overrides,
  };
}

// Mirrors Dashboard's filtersRef.current.
function defaultFilters(overrides = {}) {
  return {
    statusFilter: 'All',
    sourceFilter: 'All',
    minScore: 60,
    companySearch: '',
    titleSearch: '',
    tailoredOnly: false,
    tierFilter: 'All',
    hideExpired: true,
    sortBy: 'first_seen',
    sortOrder: 'desc',
    archetypeFilter: 'All',
    seniorityFilter: 'All',
    remoteFilter: 'All',
    levelFitFilter: 'All',
    skillFilter: '',
    ...overrides,
  };
}

function renderSection(props = {}) {
  return render(
    <MemoryRouter>
      <PastJobsSection filters={defaultFilters()} filterVersion={0} {...props} />
    </MemoryRouter>
  );
}

function resolveWith(jobs, total) {
  apiGet.mockResolvedValue({ jobs, total: total ?? jobs.length, page: 1, per_page: 25 });
}

const header = () => screen.queryByRole('button', { name: /Past \/ Outdated/ });

beforeEach(() => {
  apiGet.mockReset();
  apiDelete.mockReset();
  navigate.mockReset();
});

describe('PastJobsSection — what it asks the API for', () => {
  it('requests lifecycle=stale', async () => {
    resolveWith([makeStaleJob()]);
    renderSection();

    await waitFor(() => expect(apiGet).toHaveBeenCalled());
    expect(apiGet.mock.calls[0][0]).toContain('lifecycle=stale');
  });

  it('inherits the dashboard\'s other filters so both lists describe one corpus', async () => {
    resolveWith([makeStaleJob()]);
    renderSection({ filters: defaultFilters({ tierFilter: 'A', minScore: 70 }) });

    await waitFor(() => expect(apiGet).toHaveBeenCalled());
    const url = apiGet.mock.calls[0][0];
    expect(url).toContain('tier=A');
    expect(url).toContain('min_score=70');
    expect(url).toContain('hide_expired=true');
  });

  it('refetches when filterVersion changes, and not on an unrelated re-render', async () => {
    resolveWith([makeStaleJob()]);
    const { rerender } = renderSection();
    await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(1));

    // A new filters object with the same version — the parent rebuilds this
    // on every keystroke and it must NOT cause a refetch.
    rerender(
      <MemoryRouter>
        <PastJobsSection filters={defaultFilters()} filterVersion={0} />
      </MemoryRouter>
    );
    expect(apiGet).toHaveBeenCalledTimes(1);

    rerender(
      <MemoryRouter>
        <PastJobsSection filters={defaultFilters()} filterVersion={1} />
      </MemoryRouter>
    );
    await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(2));
  });
});

describe('PastJobsSection — staying out of the way', () => {
  it('renders nothing at all when the stale bucket is empty', async () => {
    resolveWith([], 0);
    const { container } = renderSection();

    await waitFor(() => expect(apiGet).toHaveBeenCalled());
    expect(header()).toBeNull();
    expect(container).toBeEmptyDOMElement();
  });

  it('is collapsed by default — the header shows, the jobs do not', async () => {
    resolveWith([makeStaleJob({ title: 'Platform Engineer' })], 1);
    renderSection();

    await waitFor(() => expect(header()).toBeInTheDocument());
    expect(screen.queryByText('Platform Engineer')).toBeNull();
    expect(header()).toHaveAttribute('aria-expanded', 'false');
  });

  it('degrades to a quiet notice when the request fails, instead of throwing', async () => {
    apiGet.mockRejectedValue(new Error('503 Service Unavailable'));
    renderSection();

    await waitFor(() =>
      expect(screen.getByText(/Couldn't load past \/ outdated jobs/)).toBeInTheDocument()
    );
    expect(screen.getByText(/503 Service Unavailable/)).toBeInTheDocument();
  });
});

describe('PastJobsSection — what it shows once opened', () => {
  it('reports the count and the age band in the header', async () => {
    resolveWith([makeStaleJob()], 39);
    renderSection();

    await waitFor(() => expect(header()).toBeInTheDocument());
    expect(screen.getByText('(39)')).toBeInTheDocument();
    // The band description tells the user the rule, so they know what this
    // shelf is and when its contents will disappear.
    expect(screen.getByText(/days old · archived after 30/)).toBeInTheDocument();
  });

  it('expands to reveal each job with its age', async () => {
    resolveWith([
      makeStaleJob({ job_id: 's1', title: 'Platform Engineer', first_seen: daysAgo(27) }),
      makeStaleJob({ job_id: 's2', title: 'Data Engineer', first_seen: daysAgo(14) }),
    ], 2);
    renderSection();

    await waitFor(() => expect(header()).toBeInTheDocument());
    fireEvent.click(header());

    expect(screen.getByText('Platform Engineer')).toBeInTheDocument();
    expect(screen.getByText('Data Engineer')).toBeInTheDocument();
    expect(screen.getByText('27 days old')).toBeInTheDocument();
    expect(screen.getByText('14 days old')).toBeInTheDocument();
    expect(header()).toHaveAttribute('aria-expanded', 'true');
  });

  it('shows no age caption for a row with a missing first_seen', async () => {
    resolveWith([makeStaleJob({ title: 'No Timestamp Role', first_seen: null })], 1);
    renderSection();

    await waitFor(() => expect(header()).toBeInTheDocument());
    fireEvent.click(header());

    expect(screen.getByText('No Timestamp Role')).toBeInTheDocument();
    // Scoped to the per-row caption shape — the section header carries its own
    // "14–30 days old" band description, which is not a row age.
    expect(screen.queryByText(/^\d+ days? old$/)).toBeNull();
  });

  it('says how many are not shown when the bucket exceeds one page', async () => {
    resolveWith(Array.from({ length: 25 }, (_, i) =>
      makeStaleJob({ job_id: `s${i}`, title: `Role ${i}` })), 39);
    renderSection();

    await waitFor(() => expect(header()).toBeInTheDocument());
    fireEvent.click(header());

    expect(screen.getByText(/Showing 25 of 39/)).toBeInTheDocument();
  });

  it('does not claim to be truncated when the whole bucket fits', async () => {
    resolveWith([makeStaleJob()], 1);
    renderSection();

    await waitFor(() => expect(header()).toBeInTheDocument());
    fireEvent.click(header());

    expect(screen.queryByText(/Showing \d+ of/)).toBeNull();
  });

  it('opens the job workspace when a past job is clicked', async () => {
    resolveWith([makeStaleJob({ job_id: 'abc123', title: 'Platform Engineer' })], 1);
    renderSection();

    await waitFor(() => expect(header()).toBeInTheDocument());
    fireEvent.click(header());
    fireEvent.click(screen.getByText('Platform Engineer'));

    expect(navigate).toHaveBeenCalledWith('/jobs/abc123');
  });

  it('renders a missing score as "--" rather than blank or NaN', async () => {
    resolveWith([makeStaleJob({ match_score: null, score_tier: null })], 1);
    renderSection();

    await waitFor(() => expect(header()).toBeInTheDocument());
    fireEvent.click(header());

    expect(screen.getByText('--')).toBeInTheDocument();
  });
});
