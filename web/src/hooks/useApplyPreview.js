import { useEffect, useState, useCallback } from 'react'
import { apiGet } from '../api'

export function useApplyPreview(jobId, { enabled = true } = {}) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [isLoading, setIsLoading] = useState(enabled)

  const fetcher = useCallback(async () => {
    setIsLoading(true)
    setError(null)
    try {
      const result = await apiGet(`/api/apply/preview/${jobId}`)
      setData(result)
    } catch (e) {
      setError(e)
      setData(null)
    } finally {
      setIsLoading(false)
    }
  }, [jobId])

  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    ;(async () => {
      setIsLoading(true)
      setError(null)
      try {
        const result = await apiGet(`/api/apply/preview/${jobId}`)
        if (!cancelled) setData(result)
      } catch (e) {
        if (!cancelled) {
          setError(e)
          setData(null)
        }
      } finally {
        if (!cancelled) setIsLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [enabled, jobId])

  return { data, error, isLoading, refetch: fetcher }
}
