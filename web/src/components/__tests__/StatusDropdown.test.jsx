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
