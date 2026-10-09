/**
 * Complete Setup must land on "You're All Set!", every time.
 *
 * Live E2E against production (2026-10-09): once, Complete Setup skipped the
 * Done step and went straight to the Dashboard.
 *
 * Mechanism: handleComplete awaits refetchProfile() so AppLayout will see
 * onboarding_completed_at, then calls next() to show Done. But the wizard
 * also has a defensive effect -- "an already-onboarded user should not be in
 * the wizard, send them to /" -- and it watches the same ProfileContext. The
 * refetch is precisely what makes that effect fire. Whether Done was ever
 * painted depended on how the profile update and setStep(4) were batched
 * relative to each other, i.e. on network timing.
 *
 * The context double below is stateful (useSyncExternalStore), so the
 * refetch really re-renders the wizard the way ProfileProvider does.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useSyncExternalStore } from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'

vi.mock('../../auth/useAuth', () => ({ useAuth: vi.fn() }))
vi.mock('../../hooks/useUserProfile', () => ({ useUserProfile: vi.fn() }))
vi.mock('../../api', () => ({
  apiGet: vi.fn(),
  apiPut: vi.fn(async () => ({})),
  apiUpload: vi.fn(),
}))

import { useAuth } from '../../auth/useAuth'
import { useUserProfile } from '../../hooks/useUserProfile'
import { apiGet } from '../../api'
import Onboarding from '../Onboarding'

// app.py ProfileResponse, filled enough that Complete Setup is enabled.
const MID_WIZARD = {
  id: 'u1', email: 'a@b.c', full_name: 'Jane Doe', phone: '+353 1',
  linkedin_url: 'https://linkedin.com/in/jane', visa_status: 'Stamp 1G',
  work_authorizations: { Ireland: 'stamp_1g' }, notice_period_text: '1 month',
  created_at: '2026-10-01T10:00:00+00:00', onboarding_completed_at: null,
  profile_complete: false,
}
const COMPLETED = {
  ...MID_WIZARD, onboarding_completed_at: '2026-10-09T10:05:00+00:00', profile_complete: true,
}

// A tiny external store standing in for ProfileProvider's state.
let ctx
const listeners = new Set()
const setCtx = (patch) => { ctx = { ...ctx, ...patch }; listeners.forEach((l) => l()) }
const subscribe = (l) => { listeners.add(l); return () => listeners.delete(l) }

beforeEach(() => {
  vi.clearAllMocks()
  listeners.clear()
  useAuth.mockReturnValue({ user: { id: 'u1', email: 'a@b.c' }, loading: false })
  apiGet.mockImplementation(async (ep) =>
    ep === '/api/profile' ? { ...MID_WIZARD } : { queries: ['sre'], experience_levels: ['mid_level'] },
  )
  ctx = { profile: MID_WIZARD, isLoading: false, error: null, refetch: null }
  useUserProfile.mockImplementation(() => useSyncExternalStore(subscribe, () => ctx))
})

function renderWizard() {
  return render(
    <MemoryRouter initialEntries={['/onboarding']}>
      <Routes>
        <Route path="/onboarding" element={<Onboarding />} />
        <Route path="/" element={<div>DASHBOARD</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

async function completeSetup() {
  renderWizard()
  // Welcome -> Resume -> Profile -> Preferences
  for (let i = 0; i < 3; i++) fireEvent.click(screen.getByRole('button', { name: /Next/ }))
  const complete = screen.getByRole('button', { name: /Complete Setup/ })
  await waitFor(() => expect(complete).not.toBeDisabled()) // profile prefill landed
  fireEvent.click(complete)
}

async function expectDoneStepStays() {
  expect(await screen.findByText("You're All Set!")).toBeInTheDocument()
  await new Promise((r) => setTimeout(r, 50))
  expect(screen.getByText("You're All Set!")).toBeInTheDocument()
  expect(screen.queryByText('DASHBOARD')).not.toBeInTheDocument()
}

describe('Onboarding Complete Setup -> Done step', () => {
  it('shows Done when the refetched profile lands in the same render as the step change', async () => {
    // ProfileProvider.fetchProfile: isLoading on, GET, setProfile + isLoading
    // off, then handleComplete resumes and calls next().
    ctx.refetch = async () => {
      setCtx({ isLoading: true })
      await new Promise((r) => setTimeout(r, 10))
      setCtx({ profile: COMPLETED, isLoading: false })
    }
    await completeSetup()
    await expectDoneStepStays()
  })

  it('shows Done when the refetched profile commits before the step change', async () => {
    ctx.refetch = async () => {
      setCtx({ isLoading: true })
      await new Promise((r) => setTimeout(r, 10))
      setCtx({ profile: COMPLETED, isLoading: false })
      // Give React a macrotask to commit (and run effects) before resolving.
      await new Promise((r) => setTimeout(r, 10))
    }
    await completeSetup()
    await expectDoneStepStays()
  })

  it('"Go to Dashboard" from Done still navigates', async () => {
    ctx.refetch = async () => { setCtx({ profile: COMPLETED }) }
    await completeSetup()
    fireEvent.click(await screen.findByRole('button', { name: /Go to Dashboard/ }))
    expect(await screen.findByText('DASHBOARD')).toBeInTheDocument()
  })

  it('an already-onboarded user opening /onboarding is still sent to the dashboard', async () => {
    ctx = { ...ctx, profile: COMPLETED }
    renderWizard()
    expect(await screen.findByText('DASHBOARD')).toBeInTheDocument()
  })
})
