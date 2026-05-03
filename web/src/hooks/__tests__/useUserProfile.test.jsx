import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { ProfileProvider, useUserProfile } from '../useUserProfile'

vi.mock('../../api', () => ({
  apiGet: vi.fn(),
}))
import { apiGet } from '../../api'

vi.mock('../../auth/useAuth', () => ({
  useAuth: vi.fn(() => ({ user: { id: 'u1', email: 'a@b.com' }, loading: false })),
}))
import { useAuth } from '../../auth/useAuth'

const wrapper = ({ children }) => <ProfileProvider>{children}</ProfileProvider>

describe('useUserProfile', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAuth.mockReturnValue({ user: { id: 'u1', email: 'a@b.com' }, loading: false })
  })

  it('starts loading, then exposes profile from /api/profile', async () => {
    apiGet.mockResolvedValueOnce({
      id: 'u1', email: 'a@b.com', profile_complete: true, full_name: 'Daisy',
    })

    const { result } = renderHook(() => useUserProfile(), { wrapper })

    expect(result.current.isLoading).toBe(true)
    expect(result.current.profile).toBeNull()

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.profile.profile_complete).toBe(true)
    expect(apiGet).toHaveBeenCalledWith('/api/profile')
  })

  it('exposes profile_complete=false when backend says incomplete', async () => {
    apiGet.mockResolvedValueOnce({ id: 'u1', email: 'a@b.com', profile_complete: false })

    const { result } = renderHook(() => useUserProfile(), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.profile.profile_complete).toBe(false)
  })

  it('exposes profile=null on fetch error', async () => {
    apiGet.mockRejectedValueOnce(new Error('network down'))

    const { result } = renderHook(() => useUserProfile(), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.profile).toBeNull()
  })

  it('returns safe defaults when called outside ProfileProvider', () => {
    // No wrapper — context returns its default value.
    const { result } = renderHook(() => useUserProfile())
    expect(result.current.profile).toBeNull()
    expect(result.current.isLoading).toBe(true)
    expect(typeof result.current.refetch).toBe('function')
  })

  it('refetches profile when user changes (sign-out → sign-in-as-different-user)', async () => {
    apiGet.mockResolvedValueOnce({ id: 'u1', email: 'a@b.com', profile_complete: true })

    const { rerender } = renderHook(() => useUserProfile(), { wrapper })

    await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(1))

    // Now simulate sign-in-as-different-user
    useAuth.mockReturnValue({ user: { id: 'u2', email: 'c@d.com' }, loading: false })
    apiGet.mockResolvedValueOnce({ id: 'u2', email: 'c@d.com', profile_complete: false })

    rerender()

    await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(2))
  })

  it('clears profile and skips fetch when user is null (logged out)', async () => {
    useAuth.mockReturnValue({ user: null, loading: false })

    const { result } = renderHook(() => useUserProfile(), { wrapper })

    // Wait a tick — useEffect should run, NOT fetch, set isLoading false
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(apiGet).not.toHaveBeenCalled()
    expect(result.current.profile).toBeNull()
  })

  it('exposes refetch() that re-pulls /api/profile and updates the value', async () => {
    // Regression: Onboarding "Complete Setup" must be able to refresh the
    // ProfileContext after saving. Without this, AppLayout's gate sees stale
    // `onboarding_completed_at = null` and bounces the user back to /onboarding
    // — an infinite loop. See Onboarding.handleComplete.
    useAuth.mockReturnValue({ user: { id: 'u1' }, loading: false })
    apiGet.mockResolvedValueOnce({ id: 'u1', full_name: 'Old', onboarding_completed_at: null })

    const { result } = renderHook(() => useUserProfile(), { wrapper })
    await waitFor(() => expect(result.current.profile?.full_name).toBe('Old'))
    expect(typeof result.current.refetch).toBe('function')

    // Simulate the Onboarding completion: backend now returns fresh profile.
    apiGet.mockResolvedValueOnce({ id: 'u1', full_name: 'Old', onboarding_completed_at: '2026-05-03T00:00:00Z' })

    await result.current.refetch()
    await waitFor(() => expect(result.current.profile?.onboarding_completed_at).toBe('2026-05-03T00:00:00Z'))
    expect(apiGet).toHaveBeenCalledTimes(2)
  })

  it('refetch() is a noop when called outside a ProfileProvider (no crash)', async () => {
    // Default context value exposes a noop refetch so consumers calling it
    // outside the provider don't throw.
    const { result } = renderHook(() => useUserProfile())
    expect(typeof result.current.refetch).toBe('function')
    await expect(result.current.refetch()).resolves.toBeUndefined()
  })
})
