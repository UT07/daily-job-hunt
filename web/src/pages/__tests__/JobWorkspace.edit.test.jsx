/**
 * JobWorkspace — the Overview "Job Details" edit form.
 *
 * Verified on main at ab7ae3f: the form seeded `editFields` with title,
 * company, location and apply_url; `handleSave` PATCHed all four, merged all
 * four into local state, and set `{type:'success', message:'Job updated.'}`.
 * app.py's `_EDITABLE_FIELDS` was `{application_status, location, apply_url}`
 * and filtered the body, so title and company never reached the database. The
 * request still answered 200 because `location`/`apply_url` made `update_data`
 * non-empty. Net effect: edit the title, see the new title, read "Job updated.",
 * get the old title back on reload.
 *
 * `jobSaveOutcome.test.js` covers the decision in isolation. This file exists
 * because a correct helper that nothing calls changes nothing (CLAUDE.md rule
 * 10) — these tests drive the real component and read the real banner.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const { apiGet, apiPatch, apiCall } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPatch: vi.fn(),
  apiCall: vi.fn(),
}));
vi.mock('../../api', () => ({ apiGet, apiPatch, apiCall }));

// Routing: the component only needs the job id and a no-op navigate.
vi.mock('react-router-dom', () => ({
  useParams: () => ({ jobId: 'job-1' }),
  useNavigate: () => vi.fn(),
}));

// EmailComposer talks to its own endpoints and has nothing to do with the form.
vi.mock('../../components/EmailComposer', () => ({
  default: () => <div data-testid="email-composer-stub" />,
}));

import JobWorkspace from '../JobWorkspace';

const JOB = {
  job_id: 'job-1',
  title: 'Sinor Backend Enginer',   // deliberately as the scraper mangled it
  company: 'Acme Corp',
  location: 'Dublin',
  apply_url: 'https://example.com/apply',
  application_status: 'New',
  description: 'Build things.',
  match_score: 88,
};

beforeEach(() => {
  apiGet.mockReset();
  apiPatch.mockReset();
  apiCall.mockReset();
  apiGet.mockImplementation((url) => {
    if (url === '/api/dashboard/jobs/job-1') return Promise.resolve({ ...JOB });
    // versions + timeline are auxiliary
    return Promise.resolve([]);
  });
});

async function openEditForm() {
  render(<JobWorkspace />);
  const editBtn = await screen.findByTestId('apply-url-edit');
  fireEvent.click(editBtn);
  return editBtn;
}

describe('Job Details edit form', () => {
  it('does not offer title or company as editable inputs', async () => {
    // The honest fix for a field the backend will not store: show it, do not
    // pretend it can be changed. merge_dedup.py builds its cross-source dedup
    // key from the stored jobs.company + jobs.title.
    await openEditForm();

    expect(screen.getByTestId('job-readonly-title')).toHaveTextContent('Sinor Backend Enginer');
    expect(screen.getByTestId('job-readonly-company')).toHaveTextContent('Acme Corp');

    // The editable inputs are exactly location + apply_url.
    expect(screen.getByLabelText('Location')).toBeInTheDocument();
    expect(screen.getByLabelText('Apply URL')).toBeInTheDocument();
    expect(screen.queryByLabelText('Title')).toBeNull();
    expect(screen.queryByLabelText('Company')).toBeNull();
  });

  it('sends only the fields the backend accepts', async () => {
    await openEditForm();

    fireEvent.change(screen.getByLabelText('Location'), { target: { value: 'Cork' } });
    apiPatch.mockResolvedValue({ ...JOB, location: 'Cork' });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(apiPatch).toHaveBeenCalled());
    const [, body] = apiPatch.mock.calls[0];
    // Under the bug this body carried title and company too.
    expect(Object.keys(body).sort()).toEqual(['apply_url', 'location']);
    expect(body.location).toBe('Cork');
  });

  it('shows "Job updated." only when the server echoes the new values back', async () => {
    await openEditForm();

    fireEvent.change(screen.getByLabelText('Location'), { target: { value: 'Cork' } });
    apiPatch.mockResolvedValue({ ...JOB, location: 'Cork' });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    expect(await screen.findByText('Job updated.')).toBeInTheDocument();
    // Form closed, and the displayed value is the stored one.
    await waitFor(() => expect(screen.queryByLabelText('Location')).toBeNull());
    expect(screen.getByText('Cork')).toBeInTheDocument();
  });

  it('does NOT show success when the server did not store what was sent', async () => {
    // The precise shape of the original defect, now applied to a field the form
    // does send: a 200 whose row came back without the edit. Before the fix,
    // handleSave ignored the response entirely and reported success here.
    await openEditForm();

    fireEvent.change(screen.getByLabelText('Location'), { target: { value: 'Cork' } });
    apiPatch.mockResolvedValue({ ...JOB, location: 'Dublin' }); // unchanged
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    expect(await screen.findByText(/Not saved: location/)).toBeInTheDocument();
    expect(screen.queryByText('Job updated.')).toBeNull();
    // Still editing, so the typed value is there to retry.
    expect(screen.getByLabelText('Location')).toHaveValue('Cork');
  });

  it('does NOT show success when the response omits the field entirely', async () => {
    await openEditForm();

    fireEvent.change(screen.getByLabelText('Apply URL'), {
      target: { value: 'https://example.com/new' },
    });
    apiPatch.mockResolvedValue({ job_id: 'job-1' }); // 200, but no apply_url
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    expect(await screen.findByText(/Not saved:/)).toBeInTheDocument();
    expect(screen.getByText(/apply_url/)).toBeInTheDocument();
    expect(screen.queryByText('Job updated.')).toBeNull();
  });

  it('surfaces a rejected field from the backend as an error, not a success', async () => {
    // What a future field added to the form without adding it to
    // app._EDITABLE_FIELDS now looks like: a 400, caught, shown.
    await openEditForm();

    fireEvent.change(screen.getByLabelText('Location'), { target: { value: 'Cork' } });
    apiPatch.mockRejectedValue(new Error("Field(s) not editable: ['notes']"));
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    expect(await screen.findByText(/Save failed/)).toBeInTheDocument();
    expect(screen.queryByText('Job updated.')).toBeNull();
  });
});
