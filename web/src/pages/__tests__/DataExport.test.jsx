/**
 * Data & Privacy page: two statements that did not match the server.
 *
 * 1. Consent status read ONLY localStorage (hasLocalConsent). A fresh browser
 *    said "You have not yet provided consent" for a user whose row has
 *    gdpr_consent_at -- ProfileResponse returns it.
 *
 * 2. "Delete My Account" reported "permanently deleted". app.py's DELETE
 *    /api/gdpr/delete calls gdpr.request_deletion, which only sets
 *    gdpr_deletion_requested_at and answers
 *    {status: "deletion_requested", message: "...permanently deleted in 30
 *    days. Contact support to cancel.", gdpr_deletion_requested_at}. The hard
 *    delete is scripts/data_retention.py, a separate job.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../auth/useAuth', () => ({ useAuth: vi.fn() }));
vi.mock('../../hooks/useUserProfile', () => ({ useUserProfile: vi.fn() }));
vi.mock('../../api', () => ({ apiGetBlob: vi.fn(), apiDelete: vi.fn() }));

import { useAuth } from '../../auth/useAuth';
import { useUserProfile } from '../../hooks/useUserProfile';
import { apiDelete } from '../../api';
import { setLocalConsent } from '../../lib/userStorage';
import DataExport from '../DataExport';

const USER = { id: 'u1', email: 'a@b.c' };
const PROFILE = { id: 'u1', email: 'a@b.c', full_name: 'Jane Doe', gdpr_consent_at: null };

function renderPage(profile) {
  useUserProfile.mockReturnValue({ profile, isLoading: false, error: null });
  return render(<MemoryRouter><DataExport /></MemoryRouter>);
}

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  useAuth.mockReturnValue({ user: USER, loading: false, signOut: vi.fn(async () => {}) });
});

describe('Consent status', () => {
  it('trusts the server record in a browser with no local flag', () => {
    renderPage({ ...PROFILE, gdpr_consent_at: '2026-09-01T10:00:00+00:00' });
    expect(screen.getByText('You have consented to data processing.')).toBeInTheDocument();
  });

  it('says not consented when the server has no record', () => {
    renderPage(PROFILE);
    expect(screen.getByText(/You have not yet provided consent/)).toBeInTheDocument();
  });

  it('reflects consent just given in this browser before the profile is re-read', () => {
    // ConsentBanner writes the local flag only after the server accepted it.
    setLocalConsent('u1');
    renderPage(PROFILE);
    expect(screen.getByText('You have consented to data processing.')).toBeInTheDocument();
  });
});

describe('Delete My Account copy', () => {
  it('reports a deletion request, not a completed permanent deletion', async () => {
    apiDelete.mockResolvedValue({
      status: 'deletion_requested',
      message: 'Your account will be permanently deleted in 30 days. Contact support to cancel.',
      gdpr_deletion_requested_at: '2026-10-09T10:00:00',
    });
    renderPage(PROFILE);
    fireEvent.click(screen.getByRole('button', { name: /Delete My Account/ }));
    fireEvent.change(screen.getByPlaceholderText('Type DELETE to confirm'), { target: { value: 'DELETE' } });
    fireEvent.click(screen.getByRole('button', { name: /Confirm Delete/ }));

    const status = await screen.findByText(/Deletion requested/);
    expect(status).toHaveTextContent(/30-day grace period/);
    expect(status).toHaveTextContent(/signed out/);
    expect(screen.queryByText(/have been permanently deleted/)).not.toBeInTheDocument();
  });

  it('does not describe the action as immediate and irreversible before it runs', () => {
    renderPage(PROFILE);
    expect(screen.queryByText(/This action is\s+irreversible/)).not.toBeInTheDocument();
    expect(screen.getByText(/30-day grace period/)).toBeInTheDocument();
  });
});
