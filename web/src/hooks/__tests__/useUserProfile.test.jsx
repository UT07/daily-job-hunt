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
    expect(result.current).toEqual({ profile: null, isLoading: true })
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
})
