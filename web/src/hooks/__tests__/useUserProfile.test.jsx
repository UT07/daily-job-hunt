import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { ProfileProvider, useUserProfile } from '../useUserProfile'

vi.mock('../../api', () => ({
  apiGet: vi.fn(),
}))
import { apiGet } from '../../api'

const wrapper = ({ children }) => <ProfileProvider>{children}</ProfileProvider>

describe('useUserProfile', () => {
  beforeEach(() => vi.clearAllMocks())

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
})
