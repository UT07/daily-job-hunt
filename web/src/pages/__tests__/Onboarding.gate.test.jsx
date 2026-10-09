/**
 * Who gets to see the onboarding wizard, and who gets sent past it.
 *
 * 1. /onboarding had no auth guard. It is routed outside AppLayout, so a
 *    signed-out visitor saw the wizard and every call it made 401'd.
 *
 * 2. A reload mid-wizard skipped onboarding. Step 1's résumé upload writes
 *    users.name (app.py upload_resume -> profile_updates["name"]), and both
 *    AppLayout's gate and Onboarding's redirect treated `full_name` as
 *    "onboarded". So upload, reload, and you were on the Dashboard with no
 *    preferences, no visa status, and `onboarding_completed_at` still null.
 *
 * Profile shapes are app.py's ProfileResponse (full_name, created_at,
 * onboarding_completed_at, profile_complete, ...).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

vi.mock('../../auth/useAuth', () => ({ useAuth: vi.fn() }));
vi.mock('../../hooks/useUserProfile', () => ({ useUserProfile: vi.fn() }));
vi.mock('../../api', () => ({
  apiGet: vi.fn(() => Promise.resolve({})),
  apiPut: vi.fn(),
  apiUpload: vi.fn(),
}));
// The shell's chrome is irrelevant to the gate.
vi.mock('../../components/layout/Sidebar', () => ({ default: () => null }));
vi.mock('../../components/layout/MobileNav', () => ({ default: () => null }));
vi.mock('../../components/ConsentBanner', () => ({ default: () => null }));
vi.mock('../../components/FinishSetupBanner', () => ({ default: () => null }));

import { useAuth } from '../../auth/useAuth';
import { useUserProfile } from '../../hooks/useUserProfile';
import Onboarding from '../Onboarding';
import AppLayout from '../../layouts/AppLayout';
import { isOnboarded, LEGACY_ONBOARDING_CUTOFF } from '../../lib/onboarding';

const USER = { id: 'u1', email: 'a@b.c' };

// Mid-wizard: the résumé upload has written a name; Complete Setup has not run.
const MID_WIZARD = {
  id: 'u1', email: 'a@b.c', full_name: 'Jane Doe',
  created_at: '2026-10-01T10:00:00+00:00',
  onboarding_completed_at: null, profile_complete: false,
};
const COMPLETED = { ...MID_WIZARD, onboarding_completed_at: '2026-10-01T10:05:00+00:00' };
// Onboarded before the column existed (added 2026-04-13, no backfill).
const LEGACY = { ...MID_WIZARD, created_at: '2026-03-24T12:00:00+00:00' };

function profileCtx(profile) {
  useUserProfile.mockReturnValue({
    profile, isLoading: false, error: null, refetch: vi.fn(async () => {}),
  });
}

function renderAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/login" element={<div>LOGIN PAGE</div>} />
        <Route path="/onboarding" element={<Onboarding />} />
        <Route element={<AppLayout />}>
          <Route index element={<div>DASHBOARD</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  useAuth.mockReset();
  useUserProfile.mockReset();
});

describe('/onboarding auth guard', () => {
  it('sends a signed-out visitor to /login', async () => {
    useAuth.mockReturnValue({ user: null, loading: false });
    profileCtx(null);
    renderAt('/onboarding');
    expect(await screen.findByText('LOGIN PAGE')).toBeInTheDocument();
  });

  it('waits for auth instead of redirecting while it is loading', () => {
    useAuth.mockReturnValue({ user: null, loading: true });
    useUserProfile.mockReturnValue({ profile: null, isLoading: true, error: null, refetch: vi.fn() });
    renderAt('/onboarding');
    expect(screen.queryByText('LOGIN PAGE')).not.toBeInTheDocument();
  });
});

describe('a name alone does not mean onboarded', () => {
  it('AppLayout sends a mid-wizard user back to onboarding', async () => {
    useAuth.mockReturnValue({ user: USER, loading: false });
    profileCtx(MID_WIZARD);
    renderAt('/');
    expect(await screen.findByText(/Resume/)).toBeInTheDocument(); // wizard step labels
    expect(screen.queryByText('DASHBOARD')).not.toBeInTheDocument();
  });

  it('Onboarding does not redirect a mid-wizard user to the dashboard', async () => {
    useAuth.mockReturnValue({ user: USER, loading: false });
    profileCtx(MID_WIZARD);
    renderAt('/onboarding');
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByText('DASHBOARD')).not.toBeInTheDocument();
  });

  it('a completed user reaches the dashboard', async () => {
    useAuth.mockReturnValue({ user: USER, loading: false });
    profileCtx(COMPLETED);
    renderAt('/');
    expect(await screen.findByText('DASHBOARD')).toBeInTheDocument();
  });

  it('a legacy user (created before the column existed) is not trapped', async () => {
    useAuth.mockReturnValue({ user: USER, loading: false });
    profileCtx(LEGACY);
    renderAt('/');
    expect(await screen.findByText('DASHBOARD')).toBeInTheDocument();
  });
});

describe('isOnboarded', () => {
  it('reads onboarding_completed_at, with a name-based fallback only before the cutoff', () => {
    expect(isOnboarded(null)).toBe(false);
    expect(isOnboarded(COMPLETED)).toBe(true);
    expect(isOnboarded(MID_WIZARD)).toBe(false);
    expect(isOnboarded(LEGACY)).toBe(true);
    // Legacy without a name never finished anything.
    expect(isOnboarded({ ...LEGACY, full_name: null })).toBe(false);
    // Boundary: created exactly at the cutoff is NOT legacy.
    expect(isOnboarded({ ...MID_WIZARD, created_at: LEGACY_ONBOARDING_CUTOFF })).toBe(false);
    // Missing/garbled created_at does not grant the fallback.
    expect(isOnboarded({ ...MID_WIZARD, created_at: null })).toBe(false);
    expect(isOnboarded({ ...MID_WIZARD, created_at: 'not a date' })).toBe(false);
  });
});
