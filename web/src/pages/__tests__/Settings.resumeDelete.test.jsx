/**
 * Deleting a résumé in Settings: one click, no confirmation, and a failure
 * went only to console.warn. The résumé deleted is the one tailoring starts
 * from, so a stray click was destructive, and a failed delete looked like a
 * click that did nothing.
 *
 * Shapes from app.py: GET /api/resumes -> {resumes: [...]};
 * DELETE /api/resumes/{id} -> {status: "deleted"}, 404 "Resume not found".
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ResumeSection } from '../Settings';
import * as api from '../../api';

const RESUME = { id: 'r1', resume_key: 'default', label: 'master.tex', created_at: '2026-10-01T00:00:00Z' };

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, 'apiGet').mockResolvedValue({ resumes: [RESUME] });
});

async function clickTrash() {
  render(<ResumeSection />);
  fireEvent.click(await screen.findByTitle('Delete resume'));
}

describe('Settings résumé delete', () => {
  it('asks for confirmation before deleting', async () => {
    const del = vi.spyOn(api, 'apiDelete').mockResolvedValue({ status: 'deleted' });
    await clickTrash();
    expect(del).not.toHaveBeenCalled();
    expect(screen.getByText(/Delete master\.tex\?/)).toBeInTheDocument();
  });

  it('Cancel keeps the résumé and sends nothing', async () => {
    const del = vi.spyOn(api, 'apiDelete').mockResolvedValue({ status: 'deleted' });
    await clickTrash();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(del).not.toHaveBeenCalled();
    expect(screen.getByText('master.tex')).toBeInTheDocument();
  });

  it('confirming deletes and removes the row', async () => {
    const del = vi.spyOn(api, 'apiDelete').mockResolvedValue({ status: 'deleted' });
    await clickTrash();
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(del).toHaveBeenCalledWith('/api/resumes/r1'));
    await waitFor(() => expect(screen.queryByText('master.tex')).not.toBeInTheDocument());
  });

  it('shows a failed delete instead of only logging it', async () => {
    vi.spyOn(api, 'apiDelete').mockRejectedValue(new Error('Resume not found'));
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    await clickTrash();
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(await screen.findByText(/Could not delete master\.tex: Resume not found/)).toBeInTheDocument();
    expect(screen.getByText('master.tex')).toBeInTheDocument();
  });
});
