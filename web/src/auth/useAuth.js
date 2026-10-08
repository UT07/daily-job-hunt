import { useContext } from 'react'
import { AuthContext } from './AuthProvider'
import { supabase } from '../lib/supabase'
import { clearOnExplicitSignOut } from '../lib/userStorage'

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
    // Cleared first and unconditionally: the user chose to leave, so their
    // consent flag and Add Job draft go with them even if the server
    // sign-out fails. (An expired session does not come through here and
    // keeps the draft -- see lib/userStorage.js.)
    clearOnExplicitSignOut(user?.id)
    if (noSupabase) return
    const { error } = await supabase.auth.signOut()
    if (error) throw error
  }

  return { user, session, loading, signIn, signUp, signOut, signInWithGoogle, resetPassword, updatePassword, noSupabase }
}
