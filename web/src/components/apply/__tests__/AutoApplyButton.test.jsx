import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { AutoApplyButton } from '../AutoApplyButton'

vi.mock('../../../lib/applyTelemetry', () => ({
  ineligibleActionTaken: vi.fn(),
}))
import * as t from '../../../lib/applyTelemetry'

const baseJob = { id: 'j1', apply_url: 'https://x.io', resume_s3_key: 'k', apply_platform: 'greenhouse', application_status: 'scored' }
const completeProfile = { profile_complete: true }

// AutoApplyButton uses useNavigate(); tests must render inside a Router.
function renderWithProfile(props, profile = completeProfile) {
  return render(
    <MemoryRouter>
      <AutoApplyButton job={baseJob} profile={profile} onOpenModal={vi.fn()} {...props} />
    </MemoryRouter>
  )
}

function renderWithJob(job) {
  return render(
    <MemoryRouter>
      <AutoApplyButton job={job} profile={completeProfile} onOpenModal={vi.fn()} />
    </MemoryRouter>
  )
}

describe('AutoApplyButton smart-button states', () => {
  beforeEach(() => vi.clearAllMocks())

  it('eligible → shows "Smart Apply" enabled', () => {
    renderWithProfile()
    const btn = screen.getByRole('button', { name: /Smart Apply/i })
    expect(btn).toBeEnabled()
  })

  it('eligible → click invokes onOpenModal', () => {
    const onOpenModal = vi.fn()
    renderWithProfile({ onOpenModal })
    fireEvent.click(screen.getByRole('button', { name: /Smart Apply/i }))
    expect(onOpenModal).toHaveBeenCalledTimes(1)
  })

  it('profile_incomplete → label changes, captures telemetry', () => {
    renderWithProfile({}, { profile_complete: false })
    expect(screen.getByRole('button', { name: /Complete profile to apply/i })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: /Complete profile to apply/i }))
    expect(t.ineligibleActionTaken).toHaveBeenCalledWith({ job_id: 'j1', reason: 'profile_incomplete' })
  })

  it('no_resume → label changes', () => {
    renderWithJob({ ...baseJob, resume_s3_key: null })
    expect(screen.getByRole('button', { name: /Generate tailored resume first/i })).toBeEnabled()
  })

  it('no_apply_url → label changes', () => {
    renderWithJob({ ...baseJob, apply_url: null })
    expect(screen.getByRole('button', { name: /Add apply URL/i })).toBeEnabled()
  })

  it('already_applied → "Applied ✓" disabled', () => {
    renderWithJob({ ...baseJob, application_status: 'applied' })
    const btn = screen.getByRole('button', { name: /Applied/i })
    expect(btn).toBeDisabled()
  })
})
