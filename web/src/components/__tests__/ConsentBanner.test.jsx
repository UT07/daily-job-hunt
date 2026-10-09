/**
 * GDPR consent: recorded only when the server recorded it, and per user.
 *
 * ConsentBanner used to catch a failed POST /api/gdpr/consent, log it, and
 * write localStorage.gdpr_consent='true' anyway, so the banner never came back
 * and the server had no consent on file. The key was global and sign-out
 * never cleared it, so the next person to sign in on the same browser
 * inherited it.
 *
 * GET /api/profile returns ProfileResponse, whose `gdpr_consent_at` is the
 * server's record (app.py).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const { apiCall, apiGet } = vi.hoisted(() => ({ apiCall: vi.fn(), apiGet: vi.fn() }));
vi.mock('../../api', () => ({ apiCall, apiGet }));
vi.mock('../../auth/useAuth', () => ({ useAuth: vi.fn() }));
import { useAuth } from '../../auth/useAuth';
import ConsentBanner from '../ConsentBanner';

const PROFILE_NO_CONSENT = { id: 'u1', email: 'a@b.com', plan: 'free', gdpr_consent_at: null, profile_complete: true };

function renderAs(userId) {
  useAuth.mockReturnValue({ user: { id: userId, email: `${userId}@b.com` }, loading: false });
  return render(<MemoryRouter><ConsentBanner /></MemoryRouter>);
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  apiGet.mockResolvedValue(PROFILE_NO_CONSENT);
});

describe('ConsentBanner', () => {
  it('a failed POST /api/gdpr/consent keeps the banner, shows an error, and stores nothing', async () => {
    apiCall.mockRejectedValue(new Error('503 Service Unavailable'));
    renderAs('u1');

    fireEvent.click(await screen.findByRole('button', { name: /accept/i }));

    expect(await screen.findByText(/couldn't record your consent/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /accept/i })).toBeInTheDocument();
    expect(localStorage.getItem('gdpr_consent:u1')).toBeNull();
    expect(localStorage.getItem('gdpr_consent')).toBeNull();
  });

  it('a successful POST stores consent under the user id and hides the banner', async () => {
    apiCall.mockResolvedValue({ status: 'ok' });
    renderAs('u1');

    fireEvent.click(await screen.findByRole('button', { name: /accept/i }));

    await waitFor(() => expect(screen.queryByRole('button', { name: /accept/i })).toBeNull());
    expect(apiCall).toHaveBeenCalledWith('/api/gdpr/consent', expect.anything());
    expect(localStorage.getItem('gdpr_consent:u1')).toBe('true');
  });

  it("one user's stored consent does not hide the banner for another", async () => {
    localStorage.setItem('gdpr_consent:u1', 'true');
    localStorage.setItem('gdpr_consent', 'true'); // the old global key
    renderAs('u2');

    expect(await screen.findByRole('button', { name: /accept/i })).toBeInTheDocument();
    expect(apiGet).toHaveBeenCalledWith('/api/profile');
  });

  it('skips the profile check when this user already consented here', async () => {
    localStorage.setItem('gdpr_consent:u1', 'true');
    renderAs('u1');
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByRole('button', { name: /accept/i })).toBeNull();
    expect(apiGet).not.toHaveBeenCalled();
  });
});
