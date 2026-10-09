/**
 * The Add Job draft is private by SCOPE, not by deletion.
 *
 * A pasted JD is unrecoverable work, so the draft must survive a session that
 * merely expired (api.js clears the session on a 401 and the user signs back
 * in). It must also never be shown to a different user on the same browser.
 * Keying the sessionStorage entry by user id gives both; the old unscoped key
 * cannot be attributed to anyone and is discarded, never restored.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const { apiCall, pollPipeline, apiGet, authState } = vi.hoisted(() => ({
  apiCall: vi.fn(), pollPipeline: vi.fn(), apiGet: vi.fn(),
  authState: { user: null },
}));
vi.mock('../../api', () => ({ apiCall, pollPipeline, apiGet }));
vi.mock('../../auth/useAuth', () => ({
  useAuth: () => ({ user: authState.user, loading: false }),
}));

import AddJob from '../AddJob';

const A_JD = 'User A pasted this Site Reliability Engineer description for Acme.';

beforeEach(() => {
  sessionStorage.clear();
});

describe('AddJob draft is scoped to the signed-in user', () => {
  // Both sides go through the component, so the key derivation is exercised
  // for the writer AND the reader -- seeding a hand-built key would pass for
  // any reader that merely avoids it.
  function typeDraftAs(userId) {
    authState.user = { id: userId, email: `${userId}@x.com` };
    const { unmount } = render(<AddJob />);
    fireEvent.change(screen.getByLabelText('Job Description'), { target: { value: A_JD } });
    fireEvent.change(screen.getByLabelText('Company'), { target: { value: 'Acme' } });
    unmount();
  }

  it("user B never sees user A's draft", () => {
    typeDraftAs('uA');
    authState.user = { id: 'uB', email: 'b@x.com' };

    render(<AddJob />);

    expect(screen.getByLabelText('Job Description')).toHaveValue('');
    expect(screen.getByLabelText('Company')).toHaveValue('');
    expect(screen.queryByText(/Restored the draft/)).toBeNull();
  });

  it('user A gets their own draft back (e.g. after re-login)', () => {
    typeDraftAs('uA');
    authState.user = { id: 'uA', email: 'a@x.com' };

    render(<AddJob />);

    expect(screen.getByLabelText('Job Description')).toHaveValue(A_JD);
    expect(screen.getByText(/Restored the draft/)).toBeInTheDocument();
  });

  it('ignores and removes the old unscoped key', () => {
    sessionStorage.setItem('naukribaba_addjob_draft', JSON.stringify({ jd: A_JD }));
    authState.user = { id: 'uB', email: 'b@x.com' };

    render(<AddJob />);

    expect(screen.getByLabelText('Job Description')).toHaveValue('');
    expect(sessionStorage.getItem('naukribaba_addjob_draft')).toBeNull();
  });
});
