import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { apiGet } from '../api'
import { useAuth } from '../auth/useAuth'

// Default `refetch: async-noop` so consumers calling refetch outside the Provider
// don't crash AND can await the call (matches Provider's promise-returning shape).
const ProfileContext = createContext({
  profile: null, isLoading: true, refetch: async () => {}, refresh: async () => false,
})

export function ProfileProvider({ children }) {
  const { user, loading: authLoading } = useAuth()
  const [profile, setProfile] = useState(null)
  const [error, setError] = useState(null)
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
      setError(null)
    } catch (err) {
      // A FAILED FETCH IS NOT AN ABSENT PROFILE. Collapsing both to
      // `profile = null` makes any 500, 502 or cold-start timeout
      // indistinguishable from "new user", and AppLayout's gate reads only
      // `profile?.onboarding_completed_at` — so a fully onboarded user is
      // redirected through the wizard, silently, with their real profile
      // intact on the server.
      //
      // The comment above records this exact bug being fixed for the AUTH
      // race on 2026-05-06 ("completed users were being bounced through the
      // wizard"). Its sibling cause was left in place. CLAUDE.md #10 — the
      // fix existed and never met the other half of the problem.
      setProfile(null)
      setError(err)
    } finally {
      setIsLoading(false)
    }
  }, [user, authLoading])

  useEffect(() => {
    fetchProfile()
  }, [fetchProfile])

  // Background re-read after the user changed their profile (Settings save).
  // NOT `refetch`: that sets isLoading, and AppLayout swaps the whole page for
  // a spinner while `user && profileLoading` -- unmounting the page that
  // asked. And a failed background read keeps the profile we have rather than
  // setting `error`, which would replace the page with "Could not load your
  // profile" over a save that succeeded. Returns true when it refreshed.
  const refresh = useCallback(async () => {
    if (authLoading || !user) return false
    try {
      const data = await apiGet('/api/profile')
      setProfile(data)
      setError(null)
      return true
    } catch (err) {
      console.warn('Profile refresh failed; keeping the current profile:', err?.message)
      return false
    }
  }, [user, authLoading])

  // Same class of bug AuthProvider had: an object literal here is a new
  // identity on every render, so every useUserProfile() consumer re-rendered
  // whenever ProfileProvider did — and any consumer listing `refetch` or the
  // context object in an effect dep array re-ran with it.
  const value = useMemo(
    () => ({ profile, isLoading, error, refetch: fetchProfile, refresh }),
    [profile, isLoading, error, fetchProfile, refresh],
  )

  return (
    <ProfileContext.Provider value={value}>
      {children}
    </ProfileContext.Provider>
  )
}

export function useUserProfile() {
  return useContext(ProfileContext)
}
