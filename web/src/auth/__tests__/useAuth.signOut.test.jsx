/**
 * Sign-out removes what belongs to the user from this browser: their consent
 * flag (any user-scoped key, plus the old global one) and the Add Job draft,
 * which holds a pasted job description. Before, signOut only called supabase,
 * so the next person on the machine inherited both.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

const { signOut } = vi.hoisted(() => ({ signOut: vi.fn() }));
vi.mock('../../lib/supabase', () => ({ supabase: { auth: { signOut } } }));

import { useAuth } from '../useAuth';

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  signOut.mockReset().mockResolvedValue({ error: null });
});

describe('useAuth().signOut', () => {
  it('clears user-scoped consent and the Add Job draft', async () => {
    localStorage.setItem('gdpr_consent:u1', 'true');
    localStorage.setItem('gdpr_consent', 'true');
    localStorage.setItem('naukribaba_view', 'card'); // a device preference: kept
    sessionStorage.setItem('naukribaba_addjob_draft', JSON.stringify({ jd: 'secret JD' }));

    const { result } = renderHook(() => useAuth());
    await act(() => result.current.signOut());

    expect(signOut).toHaveBeenCalled();
    expect(localStorage.getItem('gdpr_consent:u1')).toBeNull();
    expect(localStorage.getItem('gdpr_consent')).toBeNull();
    expect(sessionStorage.getItem('naukribaba_addjob_draft')).toBeNull();
    expect(localStorage.getItem('naukribaba_view')).toBe('card');
  });

  it('still clears local data when the server sign-out errors', async () => {
    signOut.mockResolvedValue({ error: new Error('network') });
    sessionStorage.setItem('naukribaba_addjob_draft', JSON.stringify({ jd: 'secret JD' }));

    const { result } = renderHook(() => useAuth());
    await act(async () => {
      await expect(result.current.signOut()).rejects.toThrow('network');
    });
    expect(sessionStorage.getItem('naukribaba_addjob_draft')).toBeNull();
  });
});
