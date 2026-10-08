import { useContext } from 'react'
import { AuthContext } from './AuthProvider'
import { supabase } from '../lib/supabase'
import { clearUserScopedStorage } from '../lib/userStorage'

export function useAuth() {
  const { user, session, loading } = useContext(AuthContext)

  const noSupabase = !supabase

  async function signIn(email, password) {
    if (noSupabase) throw new Error('Supabase not configured. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY.')
    const { error } = await supabase.auth.signInWithPassword({ email, password })
    if (error) throw error
  }

  async function signUp(email, password) {
    if (noSupabase) throw new Error('Supabase not configured. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY.')
    const { error } = await supabase.auth.signUp({ email, password })
    if (error) throw error
  }

  async function signInWithGoogle() {
    if (noSupabase) throw new Error('Supabase not configured. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY.')
    const { error } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: window.location.href },
    })
    if (error) throw error
  }

  async function resetPassword(email) {
    if (noSupabase) throw new Error('Supabase not configured. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY.')
    const { error } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: `${window.location.origin}/reset-password`,
    })
    if (error) throw error
  }

  async function updatePassword(newPassword) {
    if (noSupabase) throw new Error('Supabase not configured. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY.')
    const { error } = await supabase.auth.updateUser({ password: newPassword })
    if (error) throw error
  }

  async function signOut() {
    // Cleared first and unconditionally: consent flags and the Add Job draft
    // (a pasted JD) belong to this user, and a failed server sign-out must
    // not leave them behind for whoever uses the browser next.
    clearUserScopedStorage()
    if (noSupabase) return
    const { error } = await supabase.auth.signOut()
    if (error) throw error
  }

  return { user, session, loading, signIn, signUp, signOut, signInWithGoogle, resetPassword, updatePassword, noSupabase }
}
