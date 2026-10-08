import { createContext, useEffect, useMemo, useState } from 'react'
import { supabase } from '../lib/supabase'
import { identifyUser, resetUser } from '../lib/posthog'
import { clearUserScopedStorage } from '../lib/userStorage'

export const AuthContext = createContext({
  user: null,
  session: null,
  loading: true,
})

// supabase-js re-emits onAuthStateChange on TOKEN_REFRESHED *and* on tab
// focus / visibility change, handing us a freshly-deserialised session object
// each time. Storing it blind gave `session` — and the `user` hanging off it —
// a new identity on every tab switch, which cascaded:
//   new user identity
//     -> AuthContext value changes -> every useAuth() consumer re-renders
//     -> useUserProfile's fetchProfile (deps [user, authLoading]) is recreated
//     -> its useEffect([fetchProfile]) re-runs -> GET /api/profile
// plus the same for Dashboard's and Settings' [user] effects. That is the
// "app refetches constantly" report (diagnosed 2026-09-28).
//
// The fix is to adopt the incoming object only when something we actually
// care about moved, so a no-op re-emit leaves both state identities untouched
// and React bails out of the render entirely.
function sameSession(a, b) {
  if (a === b) return true
  if (!a || !b) return false
  return (
    a.access_token === b.access_token &&
    a.refresh_token === b.refresh_token &&
    a.expires_at === b.expires_at &&
    a.user?.id === b.user?.id
  )
}

// `user` is compared on its own, and more loosely than `session`: a genuine
// token refresh rotates access_token — so `session` must update — while
// leaving the signed-in identity untouched. Holding the previous `user`
// object across that refresh is the whole point; it is what stops the
// downstream refetch. Only `id` and `email` are read anywhere in the app
// (Sidebar, Settings, posthog.identifyUser), and `updated_at` moves whenever
// Supabase mutates the user record, so these three cover every field a
// consumer can observe.
function sameUser(a, b) {
  if (a === b) return true
  if (!a || !b) return false
  return a.id === b.id && a.email === b.email && a.updated_at === b.updated_at
}

export default function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [session, setSession] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // If Supabase isn't configured, skip auth (dev mode without Supabase)
    if (!supabase) {
      setLoading(false)
      return
    }

    // Keeping a stale session object here is safe: api.js reads the bearer
    // token straight from supabase.auth.getSession() on every request rather
    // than from this context, so nothing downstream can send an expired token
    // because we held on to the previous object.
    const applySession = (nextSession) => {
      setSession((prev) => (sameSession(prev, nextSession) ? prev : nextSession))
      setUser((prev) => {
        const next = nextSession?.user ?? null
        return sameUser(prev, next) ? prev : next
      })
    }

    // Check for existing session on mount
    supabase.auth.getSession().then(({ data: { session: currentSession } }) => {
      applySession(currentSession)
      identifyUser(currentSession?.user)
      setLoading(false)
    })

    // Listen for auth state changes
    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      (event, newSession) => {
        applySession(newSession)
        if (event === 'SIGNED_OUT') {
          resetUser()
          // Sign-outs that bypass useAuth().signOut -- an expired session
          // cleared by api.js on a 401, or a sign-out in another tab.
          clearUserScopedStorage()
        } else {
          identifyUser(newSession?.user)
        }
        setLoading(false)
      }
    )

    return () => subscription.unsubscribe()
  }, [])

  // Without this the object literal is a fresh identity on every render of
  // AuthProvider, so memoising `user`/`session` above would buy nothing —
  // consumers would still re-render on any parent render.
  const value = useMemo(() => ({ user, session, loading }), [user, session, loading])

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  )
}
