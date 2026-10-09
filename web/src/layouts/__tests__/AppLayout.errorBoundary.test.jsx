/**
 * A boundary that nothing renders changes nothing (CLAUDE.md #10): this
 * drives the real AppLayout with a page that throws and checks the shell
 * survives around the recovery UI.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

vi.mock('../../auth/useAuth', () => ({ useAuth: vi.fn() }));
vi.mock('../../hooks/useUserProfile', () => ({ useUserProfile: vi.fn() }));
vi.mock('../../lib/posthog', () => ({ captureException: vi.fn() }));
vi.mock('../../components/layout/Sidebar', () => ({ default: () => <nav>SIDEBAR</nav> }));
vi.mock('../../components/layout/MobileNav', () => ({ default: () => null }));
vi.mock('../../components/ConsentBanner', () => ({ default: () => null }));
vi.mock('../../components/FinishSetupBanner', () => ({ default: () => null }));

import { useAuth } from '../../auth/useAuth';
import { useUserProfile } from '../../hooks/useUserProfile';
import { captureException } from '../../lib/posthog';
import AppLayout from '../AppLayout';

function Broken() {
  throw new Error('page render failed');
}

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  useAuth.mockReturnValue({ user: { id: 'u1' }, loading: false });
  useUserProfile.mockReturnValue({
    profile: { onboarding_completed_at: '2026-10-01T00:00:00Z', profile_complete: true },
    isLoading: false, error: null, refetch: vi.fn(),
  });
});

describe('AppLayout error boundary', () => {
  it('contains a page crash inside the shell and reports it', () => {
    render(
      <MemoryRouter initialEntries={['/broken']}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/broken" element={<Broken />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByRole('alert')).toHaveTextContent(/Something went wrong/);
    expect(screen.getByText('SIDEBAR')).toBeInTheDocument();
    expect(captureException).toHaveBeenCalled();
  });
});
