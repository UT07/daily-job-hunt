/**
 * JobTable — the Table-view job list. Covers three audit-dashboard.md
 * findings plus the "artifact display" behavior called out separately:
 *
 * - P1-2: Location/Source/Resume Type/AI Model rendered as clickable sort
 *   headers that did nothing — db_client.py's get_jobs() only honors
 *   {first_seen, match_score, title, company, application_status,
 *   posted_date} and silently falls back to first_seen for anything else.
 *   JobTable.jsx now marks those four `sortable: false`; this file pins
 *   that only the four backend-honored columns are clickable.
 * - Status change (via StatusDropdown) and delete both fire their
 *   callbacks with the right job id from inside the real table markup,
 *   not just in StatusDropdown's own isolated unit test.
 * - Artifact display: resume/cover-letter columns read `resume_s3_url` /
 *   `cover_letter_s3_url` (NOT `tailored_pdf_path`, which the live schema
 *   populates on only 39 of 1,251 rows) and render a real link when
 *   present, a disabled placeholder when absent.
 *
 * Fixtures use `match_score` (never `final_score`, which is null on
 * nearly every live row) per the project's documented ground truth.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const { apiDelete, apiPatch } = vi.hoisted(() => ({ apiDelete: vi.fn(), apiPatch: vi.fn() }));
vi.mock('../../api', () => ({ apiDelete, apiPatch }));

import JobTable from '../JobTable';

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

function renderTable(props = {}) {
  const defaultProps = {
    jobs: [makeJob()],
    onStatusChange: vi.fn(),
    onDelete: vi.fn(),
    sortBy: 'first_seen',
    sortOrder: 'desc',
    onSortChange: vi.fn(),
  };
  const merged = { ...defaultProps, ...props };
  const utils = render(
    <MemoryRouter>
      <JobTable {...merged} />
    </MemoryRouter>
  );
  const table = utils.container.querySelector('table');
  return { ...utils, table, props: merged };
}

beforeEach(() => {
  apiDelete.mockReset().mockResolvedValue({});
  apiPatch.mockReset().mockResolvedValue({});
});

describe('JobTable — empty state', () => {
  it('shows a "no jobs" message and no table when jobs is empty', () => {
    const { container } = renderTable({ jobs: [] });
    expect(screen.getByText(/No jobs found/i)).toBeInTheDocument();
    expect(container.querySelector('table')).toBeNull();
  });
});

describe('JobTable — sort headers (audit P1-2 regression)', () => {
  const SORTABLE = [
    { label: 'Date', key: 'first_seen' },
    { label: 'Score', key: 'match_score' },
    { label: 'Title', key: 'title' },
    { label: 'Company', key: 'company' },
  ];
  const NOT_SORTABLE = ['Location', 'Source', 'Resume Type', 'AI Model', 'Skills'];

  for (const { label, key } of SORTABLE) {
    it(`clicking "${label}" calls onSortChange('${key}', 'desc') when not the active sort`, () => {
      const onSortChange = vi.fn();
      const { table } = renderTable({ sortBy: 'company', sortOrder: 'asc', onSortChange });
      fireEvent.click(within(table).getByText(label));
      expect(onSortChange).toHaveBeenCalledWith(key, 'desc');
    });

    it(`clicking "${label}" again toggles direction when it is already the active sort`, () => {
      const onSortChange = vi.fn();
      const { table } = renderTable({ sortBy: key, sortOrder: 'desc', onSortChange });
      fireEvent.click(within(table).getByText(label));
      expect(onSortChange).toHaveBeenCalledWith(key, 'asc');
    });
  }

  for (const label of NOT_SORTABLE) {
    it(`clicking "${label}" does NOT call onSortChange — backend can't honor this sort key`, () => {
      const onSortChange = vi.fn();
      const { table } = renderTable({ onSortChange });
      fireEvent.click(within(table).getByText(label));
      expect(onSortChange).not.toHaveBeenCalled();
    });
  }

  it('highlights the active column with a solid directional arrow; inactive columns get the dim hover hint', () => {
    const { table } = renderTable({ sortBy: 'match_score', sortOrder: 'asc' });
    const scoreArrow = within(table).getByText('Score').querySelector('span');
    const dateArrow = within(table).getByText('Date').querySelector('span');

    // Active column: solid yellow ▲ for ascending.
    expect(scoreArrow.textContent).toBe('▲');
    expect(scoreArrow.className).toContain('text-yellow');

    // Inactive column: same glyph is present as a hover affordance, but
    // invisible (opacity-0) and never colored — this is the "hint", not a
    // claim about actual sort state.
    expect(dateArrow.className).toContain('opacity-0');
    expect(dateArrow.className).not.toContain('text-yellow');
  });

  it('flips the active arrow to descending when sortOrder is desc', () => {
    const { table } = renderTable({ sortBy: 'match_score', sortOrder: 'desc' });
    const scoreArrow = within(table).getByText('Score').querySelector('span');
    expect(scoreArrow.textContent).toBe('▼');
  });
});

describe('JobTable — status change', () => {
  it('changing status via the dropdown calls the API and notifies the parent with the right job id', async () => {
    const onStatusChange = vi.fn();
    const { table } = renderTable({
      jobs: [makeJob({ job_id: 'job-42', application_status: 'New' })],
      onStatusChange,
    });

    // Open the dropdown (trigger button shows the current status) and pick Interview.
    fireEvent.click(within(table).getByText('New'));
    fireEvent.click(within(table).getByText('Interview'));

    await waitFor(() =>
      expect(apiPatch).toHaveBeenCalledWith('/api/dashboard/jobs/job-42', {
        application_status: 'Interview',
      })
    );
    expect(onStatusChange).toHaveBeenCalledWith('job-42', 'Interview');
  });
});

describe('JobTable — delete', () => {
  it('requires confirmation, then calls the API and notifies the parent with the right job id', async () => {
    const onDelete = vi.fn();
    const { table } = renderTable({
      jobs: [makeJob({ job_id: 'job-77' })],
      onDelete,
    });

    fireEvent.click(within(table).getByTitle('Delete job'));
    // Confirmation step — API must not fire until "Yes" is clicked.
    expect(apiDelete).not.toHaveBeenCalled();
    fireEvent.click(within(table).getByText('Yes'));

    await waitFor(() => expect(apiDelete).toHaveBeenCalledWith('/api/dashboard/jobs/job-77'));
    expect(onDelete).toHaveBeenCalledWith('job-77');
  });

  it('does not render a delete control when onDelete is not provided', () => {
    const { table } = renderTable({ onDelete: undefined });
    expect(within(table).queryByTitle('Delete job')).toBeNull();
  });
});

describe('JobTable — artifact display (resume / cover letter links)', () => {
  it('renders a real resume link when resume_s3_url is present', () => {
    const { table } = renderTable({
      jobs: [makeJob({ resume_s3_url: 'https://s3.example.com/resume.pdf' })],
    });
    const link = within(table).getByTitle('Resume PDF');
    expect(link.tagName).toBe('A');
    expect(link).toHaveAttribute('href', 'https://s3.example.com/resume.pdf');
  });

  it('renders a disabled placeholder (no link) when resume_s3_url and resume_doc_url are both absent', () => {
    const { table } = renderTable({
      jobs: [makeJob({ resume_s3_url: null, resume_doc_url: null })],
    });
    const placeholder = within(table).getByTitle('No Resume PDF');
    expect(placeholder.tagName).not.toBe('A');
  });

  it('renders a real cover letter link when cover_letter_s3_url is present', () => {
    const { table } = renderTable({
      jobs: [makeJob({ cover_letter_s3_url: 'https://s3.example.com/cover.pdf' })],
    });
    const link = within(table).getByTitle('Cover Letter');
    expect(link.tagName).toBe('A');
    expect(link).toHaveAttribute('href', 'https://s3.example.com/cover.pdf');
  });

  it('renders a disabled placeholder (no link) when cover_letter_s3_url is absent', () => {
    const { table } = renderTable({
      jobs: [makeJob({ cover_letter_s3_url: null })],
    });
    const placeholder = within(table).getByTitle('No Cover Letter');
    expect(placeholder.tagName).not.toBe('A');
  });

  it('does not render the legacy Google Doc icon for realistic live data (resume_doc_url unset)', () => {
    const { table } = renderTable({
      jobs: [makeJob({ resume_s3_url: 'https://s3.example.com/resume.pdf', resume_doc_url: null })],
    });
    expect(within(table).queryByTitle('Google Doc')).toBeNull();
  });

  it('ignores a non-URL sentinel value (e.g. a bare placeholder string) as if absent', () => {
    const { table } = renderTable({
      jobs: [makeJob({ resume_s3_url: 'pending' })],
    });
    expect(within(table).getByTitle('No Resume PDF')).toBeInTheDocument();
    expect(within(table).queryByTitle('Resume PDF')).toBeNull();
  });
});

describe('JobTable — row rendering', () => {
  it('renders title, company and score from the job record', () => {
    const { table } = renderTable({
      jobs: [makeJob({ title: 'Platform Engineer', company: 'Globex', match_score: 91 })],
    });
    expect(within(table).getByText('Platform Engineer')).toBeInTheDocument();
    expect(within(table).getByText('Globex')).toBeInTheDocument();
    expect(within(table).getByText('91')).toBeInTheDocument();
  });

  it('shows an EXPIRED badge for expired jobs', () => {
    const { table } = renderTable({ jobs: [makeJob({ is_expired: true })] });
    expect(within(table).getByText('EXPIRED')).toBeInTheDocument();
  });
});
