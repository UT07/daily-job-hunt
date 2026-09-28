/**
 * Regression test for the "app refetches constantly" chain diagnosed
 * 2026-09-28.
 *
 * supabase-js fires onAuthStateChange on TOKEN_REFRESHED *and* on tab focus /
 * visibility change, handing over a freshly-deserialised session object each
 * time. AuthProvider used to store it blind, so `user` got a new identity on
 * every tab switch; useUserProfile's fetchProfile (deps [user, authLoading])
 * was recreated, its useEffect([fetchProfile]) re-ran, and /api/profile was
 * refetched — on every single tab focus.
 *
 * These tests drive the real AuthProvider -> ProfileProvider tree and assert
 * on the fetch count, not on implementation details.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'

const { getSession, onAuthStateChange, apiGet } = vi.hoisted(() => ({
  getSession: vi.fn(),
  onAuthStateChange: vi.fn(),
  apiGet: vi.fn(),
}))

vi.mock('../../lib/supabase', () => ({
  supabase: { auth: { getSession, onAuthStateChange } },
}))
vi.mock('../../api', () => ({ apiGet }))
vi.mock('../../lib/posthog', () => ({
  identifyUser: vi.fn(),
  resetUser: vi.fn(),
}))

import AuthProvider from '../AuthProvider'
import { useAuth } from '../useAuth'
import { ProfileProvider, useUserProfile } from '../../hooks/useUserProfile'

const SESSION = {
  access_token: 'tok-1',
  refresh_token: 'ref-1',
  expires_at: 1790000000,
  user: { id: 'u1', email: 'a@b.com', updated_at: '2026-09-01T00:00:00Z' },
}

// What supabase-js actually hands back on a focus-triggered re-emit: the same
// values, an entirely new object graph.
const clone = (s) => (s === null ? null : JSON.parse(JSON.stringify(s)))

/** Captured onAuthStateChange handler, so a test can fire an auth event. */
let emitAuthEvent

function ProfileProbe() {
  const { profile, isLoading } = useUserProfile()
  return <div data-testid="profile">{isLoading ? 'loading' : (profile?.full_name ?? 'none')}</div>
}

/**
 * Counts renders of a plain useAuth() consumer — proves the memoised value.
 * A vi.fn() rather than an outer counter variable: react-hooks' compiler rules
 * reject reassigning a variable declared outside the component.
 */
const renderSpy = vi.fn()
const authConsumerRenders = () => renderSpy.mock.calls.length
function AuthProbe() {
  const { user, session } = useAuth()
  renderSpy()
  return (
    <div>
      <span data-testid="user-email">{user?.email ?? 'anon'}</span>
      <span data-testid="access-token">{session?.access_token ?? 'none'}</span>
    </div>
  )
}

function renderTree() {
  return render(
    <AuthProvider>
      <ProfileProvider>
        <AuthProbe />
        <ProfileProbe />
      </ProfileProvider>
    </AuthProvider>
  )
}

describe('AuthProvider identity stability', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    emitAuthEvent = undefined
    onAuthStateChange.mockImplementation((cb) => {
      emitAuthEvent = cb
      return { data: { subscription: { unsubscribe: vi.fn() } } }
    })
    getSession.mockResolvedValue({ data: { session: SESSION } })
    apiGet.mockResolvedValue({ id: 'u1', full_name: 'Daisy' })
  })

  async function mountAndSettle() {
    renderTree()
    await waitFor(() => expect(screen.getByTestId('profile')).toHaveTextContent('Daisy'))
    expect(apiGet).toHaveBeenCalledTimes(1)
  }

  it('does NOT re-issue the profile fetch when an equivalent session is re-emitted', async () => {
    await mountAndSettle()

    // Tab focus / visibility change: same login, brand-new object.
    const equivalent = clone(SESSION)
    expect(equivalent).not.toBe(SESSION)
    expect(equivalent.user).not.toBe(SESSION.user)

    await act(async () => { emitAuthEvent('SIGNED_IN', equivalent) })
    await act(async () => { emitAuthEvent('TOKEN_REFRESHED', clone(SESSION)) })

    expect(apiGet).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('profile')).toHaveTextContent('Daisy')
  })

  it('does not re-render useAuth() consumers on an equivalent re-emit', async () => {
    await mountAndSettle()
    const before = authConsumerRenders()

    await act(async () => { emitAuthEvent('SIGNED_IN', clone(SESSION)) })

    expect(authConsumerRenders()).toBe(before)
  })

  it('keeps the profile fetch to one call across a real token rotation', async () => {
    await mountAndSettle()

    // A genuine refresh: new tokens, same signed-in identity.
    const refreshed = { ...clone(SESSION), access_token: 'tok-2', refresh_token: 'ref-2' }
    await act(async () => { emitAuthEvent('TOKEN_REFRESHED', refreshed) })

    // The session must go through — consumers reading it should see the new
    // token — but the user identity is unchanged, so no refetch.
    expect(screen.getByTestId('access-token')).toHaveTextContent('tok-2')
    expect(apiGet).toHaveBeenCalledTimes(1)
  })

  it('still refetches when a different user signs in', async () => {
    await mountAndSettle()

    apiGet.mockResolvedValue({ id: 'u2', full_name: 'Rowan' })
    const other = {
      access_token: 'tok-9',
      refresh_token: 'ref-9',
      expires_at: 1790009999,
      user: { id: 'u2', email: 'c@d.com', updated_at: '2026-09-02T00:00:00Z' },
    }
    await act(async () => { emitAuthEvent('SIGNED_IN', other) })

    await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.getByTestId('profile')).toHaveTextContent('Rowan'))
    expect(screen.getByTestId('user-email')).toHaveTextContent('c@d.com')
  })

  it('still propagates sign-out', async () => {
    await mountAndSettle()

    await act(async () => { emitAuthEvent('SIGNED_OUT', null) })

    await waitFor(() => expect(screen.getByTestId('user-email')).toHaveTextContent('anon'))
    // Logged out clears the profile locally rather than hitting the API again.
    await waitFor(() => expect(screen.getByTestId('profile')).toHaveTextContent('none'))
    expect(apiGet).toHaveBeenCalledTimes(1)
  })

  it('picks up a user record that actually changed (same id, new updated_at)', async () => {
    await mountAndSettle()

    const renamed = clone(SESSION)
    renamed.user.email = 'new@b.com'
    renamed.user.updated_at = '2026-09-28T00:00:00Z'
    await act(async () => { emitAuthEvent('USER_UPDATED', renamed) })

    await waitFor(() => expect(screen.getByTestId('user-email')).toHaveTextContent('new@b.com'))
  })
})
