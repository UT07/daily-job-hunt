import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { apiGet } from '../api'
import { useAuth } from '../auth/useAuth'

// Default `refetch: async-noop` so consumers calling refetch outside the Provider
// don't crash AND can await the call (matches Provider's promise-returning shape).
const ProfileContext = createContext({ profile: null, isLoading: true, refetch: async () => {} })

export function ProfileProvider({ children }) {
  const { user, loading: authLoading } = useAuth()
  const [profile, setProfile] = useState(null)
  const [isLoading, setIsLoading] = useState(true)

  // Stable identity so consumers depending on `refetch` in effect deps
  // don't re-trigger on every parent render.
  const fetchProfile = useCallback(async () => {
    // CRITICAL: while auth is still loading, leave isLoading=true so
    // consumers (esp. AppLayout) keep showing the spinner instead of
    // redirecting to /onboarding on a transient (user=null, isLoading=false)
    // tick. Race that bit Utkarsh on 2026-05-06 — completed users were being
    // bounced through the wizard.
    if (authLoading) {
      setIsLoading(true)
      return
    }
    if (!user) {
      setProfile(null)
      setIsLoading(false)
      return
    }
    setIsLoading(true)
    try {
      const data = await apiGet('/api/profile')
      setProfile(data)
    } catch {
      setProfile(null)
    } finally {
      setIsLoading(false)
    }
  }, [user, authLoading])

  useEffect(() => {
    fetchProfile()
  }, [fetchProfile])

  return (
    <ProfileContext.Provider value={{ profile, isLoading, refetch: fetchProfile }}>
      {children}
    </ProfileContext.Provider>
  )
}

export function useUserProfile() {
  return useContext(ProfileContext)
}
