/**
 * Regression test for audit-dashboard.md P1-3: StatusDropdown — the fast
 * inline status control used in the job table — previously listed only 6 of
 * the 8 backend-valid statuses (app.py's _VALID_STATUSES), silently making
 * "Phone Screen" and "Accepted" impossible to set from the table. A user
 * could only reach those two via the separate Timeline control on a job's
 * Overview tab.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const { apiPatch } = vi.hoisted(() => ({ apiPatch: vi.fn() }));
vi.mock('../../api', () => ({ apiPatch }));

import StatusDropdown from '../StatusDropdown';

// Must match app.py's _VALID_STATUSES and JobWorkspace.jsx's VALID_STATUSES.
const ALL_STATUSES = [
  'New', 'Applied', 'Phone Screen', 'Interview', 'Offer', 'Rejected', 'Withdrawn', 'Accepted',
];

describe('StatusDropdown', () => {
  beforeEach(() => {
    apiPatch.mockReset();
    apiPatch.mockResolvedValue({});
  });

  it('offers all 8 backend-valid statuses, including Phone Screen and Accepted', () => {
    render(<StatusDropdown jobId="job-1" currentStatus="New" onStatusChange={() => {}} />);

    // Only the trigger button exists until the dropdown panel is opened.
    fireEvent.click(screen.getAllByRole('button')[0]);

    const optionText = screen.getAllByRole('button').map((b) => b.textContent).join(' | ');
    for (const status of ALL_STATUSES) {
      expect(optionText).toContain(status);
    }
  });

  it('picks Phone Screen: updates via the API and notifies the parent', async () => {
    const onStatusChange = vi.fn();
    render(<StatusDropdown jobId="job-1" currentStatus="New" onStatusChange={onStatusChange} />);

    fireEvent.click(screen.getAllByRole('button')[0]);
    const phoneScreenOption = screen.getAllByRole('button').find((b) => b.textContent.includes('Phone Screen'));
    fireEvent.click(phoneScreenOption);

    await waitFor(() =>
      expect(apiPatch).toHaveBeenCalledWith('/api/dashboard/jobs/job-1', {
        application_status: 'Phone Screen',
      }),
    );
    expect(onStatusChange).toHaveBeenCalledWith('job-1', 'Phone Screen');
  });

  it('picks Accepted: updates via the API and notifies the parent', async () => {
    const onStatusChange = vi.fn();
    render(<StatusDropdown jobId="job-2" currentStatus="Offer" onStatusChange={onStatusChange} />);

    fireEvent.click(screen.getAllByRole('button')[0]);
    const acceptedOption = screen.getAllByRole('button').find((b) => b.textContent.includes('Accepted'));
    fireEvent.click(acceptedOption);

    await waitFor(() =>
      expect(apiPatch).toHaveBeenCalledWith('/api/dashboard/jobs/job-2', {
        application_status: 'Accepted',
      }),
    );
    expect(onStatusChange).toHaveBeenCalledWith('job-2', 'Accepted');
  });
});

/**
 * A failed PATCH used to be console.error'd only: the badge stayed on the old
 * status with no message, so a user picking "Applied" during a 503 had no way
 * to know it was not saved. app.py's update_job answers 400 for an invalid
 * status, 404 for a missing job and 503 without a DB.
 */
describe('StatusDropdown — a failed update is visible', () => {
  beforeEach(() => {
    apiPatch.mockReset();
  });

  const option = (name) => screen.getAllByRole('button').find((b) => b.textContent.includes(name));

  it('shows the error and does not notify the parent', async () => {
    apiPatch.mockRejectedValue(new Error('Database not configured'));
    const onStatusChange = vi.fn();
    render(<StatusDropdown jobId="job-1" currentStatus="New" onStatusChange={onStatusChange} />);

    fireEvent.click(screen.getAllByRole('button')[0]);
    fireEvent.click(option('Applied'));

    expect(await screen.findByRole('alert')).toHaveTextContent(/Couldn't change status.*Database not configured/);
    expect(onStatusChange).not.toHaveBeenCalled();
    expect(screen.getAllByRole('button')[0]).toHaveTextContent('New');
  });

  it('clears the error once a later update succeeds', async () => {
    apiPatch.mockRejectedValueOnce(new Error('HTTP 503')).mockResolvedValueOnce({});
    render(<StatusDropdown jobId="job-1" currentStatus="New" onStatusChange={() => {}} />);

    fireEvent.click(screen.getAllByRole('button')[0]);
    fireEvent.click(option('Applied'));
    await screen.findByRole('alert');

    fireEvent.click(screen.getAllByRole('button')[0]);
    fireEvent.click(option('Applied'));
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  });
});
