/**
 * Saving the profile in Settings must update the shell, not only the server.
 *
 * Before: ProfileSection's handleSave PUT /api/profile and said "Profile
 * saved.", but ProfileContext (which AppLayout reads for `profile_complete`)
 * was never told. FinishSetupBanner kept saying "Your profile is incomplete"
 * until a full reload.
 *
 * The obvious fix -- call the context's `refetch` -- is wrong: refetch sets
 * isLoading, and AppLayout renders a full-screen spinner while
 * `user && profileLoading`, which unmounts Settings (and its "Profile saved."
 * message) mid-save. So this test drives the REAL ProfileProvider and
 * AppLayout and asserts both: the banner goes, and the page stays.
 *
 * Shapes: app.py ProfileResponse (full_name, profile_complete, ...).
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

vi.mock('../../auth/useAuth', () => ({ useAuth: vi.fn() }));
vi.mock('../../api', () => ({
  apiGet: vi.fn(),
  apiPut: vi.fn(),
  apiUpload: vi.fn(),
  apiDelete: vi.fn(),
}));
vi.mock('../../components/layout/Sidebar', () => ({ default: () => null }));
vi.mock('../../components/layout/MobileNav', () => ({ default: () => null }));
vi.mock('../../components/ConsentBanner', () => ({ default: () => null }));

import { useAuth } from '../../auth/useAuth';
import { apiGet, apiPut } from '../../api';
import { ProfileProvider } from '../../hooks/useUserProfile';
import AppLayout from '../../layouts/AppLayout';
import Settings from '../Settings';

const BASE = {
  id: 'u1', email: 'a@b.com', full_name: 'Jane Doe', phone: '+353 1',
  location: 'Dublin', github_url: null, linkedin_url: null, website: null,
  visa_status: 'Stamp 1G', work_authorizations: { Ireland: 'stamp_1g' },
  plan: 'free', created_at: '2026-10-01T00:00:00+00:00',
  onboarding_completed_at: '2026-10-01T00:05:00+00:00',
  salary_expectation_notes: '', notice_period_text: 'Available in 2 weeks',
};
const INCOMPLETE = { ...BASE, profile_complete: false };
const COMPLETE = { ...BASE, linkedin_url: 'https://linkedin.com/in/jane', profile_complete: true };

let serverProfile;

beforeEach(() => {
  vi.clearAllMocks();
  useAuth.mockReturnValue({ user: { id: 'u1', email: 'a@b.com' }, loading: false });
  serverProfile = INCOMPLETE;
  apiGet.mockImplementation((ep) => {
    // A real network round trip: resolving in the same tick would let a
    // loading flag set-and-clear without ever committing, and hide an unmount.
    if (ep === '/api/profile') {
      const snapshot = { ...serverProfile };
      return new Promise((r) => setTimeout(() => r(snapshot), 30));
    }
    if (ep === '/api/search-config') return Promise.resolve({ queries: ['sre'] });
    if (ep === '/api/resumes') return Promise.resolve({ resumes: [] });
    return Promise.resolve({});
  });
  apiPut.mockImplementation(async (ep) => {
    if (ep === '/api/profile') {
      serverProfile = COMPLETE;
      return { ...COMPLETE };
    }
    return {};
  });
});

function renderShell() {
  return render(
    <MemoryRouter initialEntries={['/settings']}>
      <ProfileProvider>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/settings" element={<Settings />} />
          </Route>
        </Routes>
      </ProfileProvider>
    </MemoryRouter>,
  );
}

function profileSaveButton() {
  const card = screen.getByText('Profile').closest('.space-y-6 > *');
  return Array.from(card.querySelectorAll('button')).find((b) => /Save Changes/.test(b.textContent));
}

describe('Settings profile save refreshes ProfileContext', () => {
  it('removes the Finish Setup banner without a reload, and keeps the page mounted', async () => {
    renderShell();
    expect(await screen.findByText(/Your profile is incomplete/)).toBeInTheDocument();
    await waitFor(() => expect(profileSaveButton()).not.toBeDisabled());

    fireEvent.click(profileSaveButton());

    expect(await screen.findByText('Profile saved.')).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByText(/Your profile is incomplete/)).not.toBeInTheDocument(),
    );
    // Still the same Settings instance: the success message survived.
    expect(screen.getByText('Profile saved.')).toBeInTheDocument();
  });

  it('a failed background refresh does not replace a successful save with an error page', async () => {
    renderShell();
    await waitFor(() => expect(profileSaveButton()).not.toBeDisabled());
    const original = apiGet.getMockImplementation();
    apiGet.mockImplementation((ep) =>
      ep === '/api/profile' ? Promise.reject(new Error('HTTP 502')) : original(ep),
    );

    fireEvent.click(profileSaveButton());

    expect(await screen.findByText('Profile saved.')).toBeInTheDocument();
    await waitFor(() =>
      expect(apiGet.mock.calls.filter(([ep]) => ep === '/api/profile').length).toBeGreaterThan(2),
    );
    expect(screen.queryByText(/Could not load your profile/)).not.toBeInTheDocument();
    expect(screen.getByText('Profile saved.')).toBeInTheDocument();
  });

  it('does not refresh the context when the save fails', async () => {
    apiPut.mockRejectedValue(new Error('HTTP 500'));
    renderShell();
    await waitFor(() => expect(profileSaveButton()).not.toBeDisabled());
    const profileGets = () => apiGet.mock.calls.filter(([ep]) => ep === '/api/profile').length;
    const before = profileGets();

    fireEvent.click(profileSaveButton());

    expect(await screen.findByText(/Save failed: HTTP 500/)).toBeInTheDocument();
    expect(profileGets()).toBe(before);
    expect(screen.getByText(/Your profile is incomplete/)).toBeInTheDocument();
  });
});
