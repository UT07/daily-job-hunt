/**
 * JobTable's mobile card stack: the whole card navigates to /jobs/{id}, and
 * its DeleteButton sits inside it. Only the trash icon stopped propagation;
 * the confirm step's "Yes" and "No" (and the retry button after a failure)
 * did not, so answering the confirm also navigated away -- mid-delete, on Yes.
 *
 * jsdom applies no CSS, so both the mobile stack and the desktop table are in
 * the DOM; queries are scoped to the mobile stack.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, within, waitFor } from '@testing-library/react';

const navigate = vi.fn();
vi.mock('react-router-dom', () => ({ useNavigate: () => navigate }));
const { apiDelete } = vi.hoisted(() => ({ apiDelete: vi.fn() }));
vi.mock('../../api', () => ({ apiDelete, apiPatch: vi.fn() }));

import JobTable from '../JobTable';

const JOB = {
  job_id: 'job-1', title: 'Backend Engineer', company: 'Acme', location: 'Dublin',
  match_score: 88, application_status: 'New',
};

function mobileCard(container) {
  return within(container.querySelector('.md\\:hidden'));
}

beforeEach(() => {
  navigate.mockReset();
  apiDelete.mockReset();
});

describe('JobTable mobile card', () => {
  it('still navigates when the card itself is clicked', () => {
    const { container } = render(<JobTable jobs={[JOB]} onDelete={vi.fn()} />);
    fireEvent.click(mobileCard(container).getByText('Backend Engineer'));
    expect(navigate).toHaveBeenCalledWith('/jobs/job-1');
  });

  it('"No" cancels the delete without navigating', () => {
    const { container } = render(<JobTable jobs={[JOB]} onDelete={vi.fn()} />);
    const card = mobileCard(container);
    fireEvent.click(card.getByTitle('Delete job'));
    fireEvent.click(card.getByText('No'));
    expect(navigate).not.toHaveBeenCalled();
  });

  it('"Yes" deletes without navigating', async () => {
    apiDelete.mockResolvedValue({ deleted: true });
    const onDelete = vi.fn();
    const { container } = render(<JobTable jobs={[JOB]} onDelete={onDelete} />);
    const card = mobileCard(container);
    fireEvent.click(card.getByTitle('Delete job'));
    fireEvent.click(card.getByText('Yes'));
    await waitFor(() => expect(onDelete).toHaveBeenCalledWith('job-1'));
    expect(navigate).not.toHaveBeenCalled();
  });

  it('the retry button after a failed delete does not navigate', async () => {
    apiDelete.mockRejectedValue(new Error('HTTP 500'));
    const { container } = render(<JobTable jobs={[JOB]} onDelete={vi.fn()} />);
    const card = mobileCard(container);
    fireEvent.click(card.getByTitle('Delete job'));
    fireEvent.click(card.getByText('Yes'));
    const retry = await card.findByTitle(/Delete failed: HTTP 500/);
    fireEvent.click(retry);
    expect(navigate).not.toHaveBeenCalled();
  });
});
