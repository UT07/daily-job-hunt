import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { useApplyPreview } from '../useApplyPreview'

vi.mock('../../api', () => ({ apiGet: vi.fn() }))
import { apiGet } from '../../api'

describe('useApplyPreview', () => {
  beforeEach(() => vi.clearAllMocks())

  it('starts idle until enabled, then fetches on mount', async () => {
    const payload = { eligible: true, custom_questions: [], cache_hit: false }
    apiGet.mockResolvedValueOnce(payload)

    const { result } = renderHook(() => useApplyPreview('job-1', { enabled: true }))

    expect(result.current.isLoading).toBe(true)
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.data).toEqual(payload)
    expect(apiGet).toHaveBeenCalledWith('/api/apply/preview/job-1')
  })

  it('does not fetch when enabled=false', () => {
    renderHook(() => useApplyPreview('job-1', { enabled: false }))
    expect(apiGet).not.toHaveBeenCalled()
  })

  it('exposes error on fetch failure', async () => {
    apiGet.mockRejectedValueOnce(new Error('500 server error'))

    const { result } = renderHook(() => useApplyPreview('job-1', { enabled: true }))

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.error).toBeTruthy()
    expect(result.current.data).toBeNull()
  })

  it('refetch() re-calls the endpoint', async () => {
    apiGet.mockResolvedValue({ eligible: true, custom_questions: [] })

    const { result } = renderHook(() => useApplyPreview('job-1', { enabled: true }))
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(apiGet).toHaveBeenCalledTimes(1)

    await act(async () => {
      await result.current.refetch()
    })
    expect(apiGet).toHaveBeenCalledTimes(2)
  })
})
