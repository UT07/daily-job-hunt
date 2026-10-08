/**
 * What leaves the browser when the session ends.
 *
 * An explicit, user-initiated signOut() clears consent flags and THIS user's
 * Add Job draft. A SIGNED_OUT event that did not come from signOut() -- an
 * expired session cleared by api.js on a 401, a sign-out in another tab --
 * clears consent flags only: the draft is keyed by user id, so it is already
 * private, and a pasted JD is unrecoverable work that re-login must restore.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, renderHook, act } from '@testing-library/react';

const { signOut, getSession, onAuthStateChange } = vi.hoisted(() => ({
  signOut: vi.fn(), getSession: vi.fn(), onAuthStateChange: vi.fn(),
}));
vi.mock('../../lib/supabase', () => ({ supabase: { auth: { signOut, getSession, onAuthStateChange } } }));
vi.mock('../../lib/posthog', () => ({ identifyUser: vi.fn(), resetUser: vi.fn() }));

import AuthProvider, { AuthContext } from '../AuthProvider';
import { useAuth } from '../useAuth';

const U1_DRAFT = 'naukribaba_addjob_draft:u1';
const U2_DRAFT = 'naukribaba_addjob_draft:u2';
const JD = JSON.stringify({ jd: 'secret JD' });

function signedInAs(id) {
  const wrapper = ({ children }) => (
    <AuthContext.Provider value={{ user: { id, email: `${id}@b.com` }, session: {}, loading: false }}>
      {children}
    </AuthContext.Provider>
  );
  return renderHook(() => useAuth(), { wrapper });
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  signOut.mockReset().mockResolvedValue({ error: null });
});

describe('useAuth().signOut (explicit)', () => {
  it("clears consent and the signed-in user's draft, keeps device preferences", async () => {
    localStorage.setItem('gdpr_consent:u1', 'true');
    localStorage.setItem('gdpr_consent', 'true');
    localStorage.setItem('naukribaba_view', 'card');
    sessionStorage.setItem(U1_DRAFT, JD);
    sessionStorage.setItem('naukribaba_addjob_draft', JD); // unscoped legacy

    const { result } = signedInAs('u1');
    await act(() => result.current.signOut());

    expect(signOut).toHaveBeenCalled();
    expect(localStorage.getItem('gdpr_consent:u1')).toBeNull();
    expect(localStorage.getItem('gdpr_consent')).toBeNull();
    expect(sessionStorage.getItem(U1_DRAFT)).toBeNull();
    expect(sessionStorage.getItem('naukribaba_addjob_draft')).toBeNull();
    expect(localStorage.getItem('naukribaba_view')).toBe('card');
  });

  it("does not touch another user's draft", async () => {
    sessionStorage.setItem(U2_DRAFT, JD);
    const { result } = signedInAs('u1');
    await act(() => result.current.signOut());
    expect(sessionStorage.getItem(U2_DRAFT)).toBe(JD);
  });

  it('still clears when the server sign-out errors', async () => {
    signOut.mockResolvedValue({ error: new Error('network') });
    sessionStorage.setItem(U1_DRAFT, JD);

    const { result } = signedInAs('u1');
    await act(async () => {
      await expect(result.current.signOut()).rejects.toThrow('network');
    });
    expect(sessionStorage.getItem(U1_DRAFT)).toBeNull();
  });
});

describe('AuthProvider on a SIGNED_OUT it did not initiate (e.g. a 401 expiry)', () => {
  it('keeps the draft so re-login restores it, and clears consent flags', async () => {
    let emit;
    onAuthStateChange.mockImplementation((cb) => {
      emit = cb;
      return { data: { subscription: { unsubscribe: vi.fn() } } };
    });
    getSession.mockResolvedValue({ data: { session: { access_token: 't', user: { id: 'u1' } } } });
    sessionStorage.setItem(U1_DRAFT, JD);
    localStorage.setItem('gdpr_consent:u1', 'true');

    render(<AuthProvider><div /></AuthProvider>);
    await act(async () => {});
    await act(async () => { emit('SIGNED_OUT', null); });

    expect(signOut).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(U1_DRAFT)).toBe(JD);
    expect(localStorage.getItem('gdpr_consent:u1')).toBeNull();
  });
});
