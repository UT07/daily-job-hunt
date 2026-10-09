/**
 * "Password updated successfully" must survive the auth event the change fires.
 *
 * Live E2E against production (2026-10-09): the password change returned 200
 * and the new password worked, but the confirmation never appeared, and the
 * page issued 6 GET /api/profile calls.
 *
 * Mechanism: supabase.auth.updateUser() emits USER_UPDATED before it resolves,
 * with the same user id and a new `updated_at`. AuthProvider (correctly) hands
 * out a new `user` object for that; ProfileProvider keyed its fetch on the
 * object, so it re-ran fetchProfile, which sets isLoading=true; AppLayout
 * swaps the whole page for a spinner while `user && profileLoading`, which
 * UNMOUNTS Settings. PasswordSection's setStatus then lands on a dead
 * component, and the remounted Settings starts blank and fetches again.
 *
 * This drives the real AuthProvider -> ProfileProvider -> AppLayout ->
 * Settings tree; only the network and the Supabase client are doubles, and
 * the Supabase double emits the event the way supabase-js does.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'

const { getSession, onAuthStateChange, updateUser, apiGet } = vi.hoisted(() => ({
  getSession: vi.fn(),
  onAuthStateChange: vi.fn(),
  updateUser: vi.fn(),
  apiGet: vi.fn(),
}))

vi.mock('../../lib/supabase', () => ({
  supabase: { auth: { getSession, onAuthStateChange, updateUser } },
}))
vi.mock('../../api', () => ({
  apiGet,
  apiPut: vi.fn(),
  apiUpload: vi.fn(),
  apiDelete: vi.fn(),
}))
vi.mock('../../lib/posthog', () => ({ identifyUser: vi.fn(), resetUser: vi.fn() }))
vi.mock('../../components/layout/Sidebar', () => ({ default: () => null }))
vi.mock('../../components/layout/MobileNav', () => ({ default: () => null }))
vi.mock('../../components/ConsentBanner', () => ({ default: () => null }))

import AuthProvider from '../../auth/AuthProvider'
import { ProfileProvider } from '../../hooks/useUserProfile'
import AppLayout from '../../layouts/AppLayout'
import Settings from '../Settings'

const SESSION = {
  access_token: 'tok-1',
  refresh_token: 'ref-1',
  expires_at: 1790000000,
  user: { id: 'u1', email: 'a@b.com', updated_at: '2026-10-01T00:00:00Z' },
}
const PROFILE = {
  id: 'u1', email: 'a@b.com', full_name: 'Jane Doe', phone: '+353 1',
  location: 'Dublin', visa_status: 'Stamp 1G', work_authorizations: { Ireland: 'stamp_1g' },
  created_at: '2026-10-01T00:00:00+00:00',
  onboarding_completed_at: '2026-10-01T00:05:00+00:00',
  profile_complete: true, notice_period_text: '1 month',
}

let emit
const profileGets = () => apiGet.mock.calls.filter(([ep]) => ep === '/api/profile').length

beforeEach(() => {
  vi.clearAllMocks()
  getSession.mockResolvedValue({ data: { session: SESSION } })
  onAuthStateChange.mockImplementation((cb) => {
    emit = cb
    return { data: { subscription: { unsubscribe: vi.fn() } } }
  })
  // supabase-js: updateUser PUTs /user, then awaits
  // _notifyAllSubscribers('USER_UPDATED', session-with-new-user) before
  // returning. A password change moves the user's updated_at.
  updateUser.mockImplementation(async () => {
    await new Promise((r) => setTimeout(r, 10))
    const user = { ...SESSION.user, updated_at: '2026-10-09T12:00:00Z' }
    emit('USER_UPDATED', { ...SESSION, user })
    return { data: { user }, error: null }
  })
  apiGet.mockImplementation((ep) => {
    // A real round trip, so a loading flag that is set actually commits.
    if (ep === '/api/profile') return new Promise((r) => setTimeout(() => r({ ...PROFILE }), 20))
    if (ep === '/api/search-config') return Promise.resolve({ queries: ['sre'] })
    if (ep === '/api/resumes') return Promise.resolve({ resumes: [] })
    return Promise.resolve({})
  })
})

function renderApp() {
  return render(
    <MemoryRouter initialEntries={['/settings']}>
      <AuthProvider>
        <ProfileProvider>
          <Routes>
            <Route element={<AppLayout />}>
              <Route path="/settings" element={<Settings />} />
            </Route>
          </Routes>
        </ProfileProvider>
      </AuthProvider>
    </MemoryRouter>,
  )
}

describe('Settings password change', () => {
  it('keeps "Password updated successfully" on screen after USER_UPDATED', async () => {
    renderApp()
    const newPass = await screen.findByPlaceholderText('Minimum 8 characters')
    await waitFor(() => expect(profileGets()).toBe(2)) // context + Settings form
    const before = profileGets()

    fireEvent.change(newPass, { target: { value: 'n3w-passw0rd' } })
    fireEvent.change(screen.getByPlaceholderText('Re-enter new password'), { target: { value: 'n3w-passw0rd' } })
    fireEvent.click(screen.getByRole('button', { name: /Update Password/ }))

    expect(await screen.findByText('Password updated successfully')).toBeInTheDocument()
    expect(updateUser).toHaveBeenCalledWith({ password: 'n3w-passw0rd' })
    // Let any refetch the event would have triggered settle, then re-check.
    await new Promise((r) => setTimeout(r, 80))
    expect(screen.getByText('Password updated successfully')).toBeInTheDocument()
    // A password change does not change the profile; nothing should re-read it.
    expect(profileGets()).toBe(before)
  })
})
