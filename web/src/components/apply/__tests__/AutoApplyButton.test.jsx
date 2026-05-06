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

  it('eligible greenhouse job → onOpenModal receives mode=cloud_browser', () => {
    const onOpenModal = vi.fn()
    render(
      <MemoryRouter>
        <AutoApplyButton job={{ ...baseJob, apply_platform: 'greenhouse' }} profile={completeProfile} onOpenModal={onOpenModal} />
      </MemoryRouter>
    )
    fireEvent.click(screen.getByRole('button', { name: /Smart Apply/i }))
    expect(onOpenModal).toHaveBeenCalledWith({ mode: 'cloud_browser' })
  })

  it('eligible ashby job → onOpenModal receives mode=cloud_browser', () => {
    const onOpenModal = vi.fn()
    render(
      <MemoryRouter>
        <AutoApplyButton job={{ ...baseJob, apply_platform: 'ashby' }} profile={completeProfile} onOpenModal={onOpenModal} />
      </MemoryRouter>
    )
    fireEvent.click(screen.getByRole('button', { name: /Smart Apply/i }))
    expect(onOpenModal).toHaveBeenCalledWith({ mode: 'cloud_browser' })
  })

  it('eligible non-GH/Ashby job (lever) → onOpenModal receives mode=hand_paste', () => {
    const onOpenModal = vi.fn()
    render(
      <MemoryRouter>
        <AutoApplyButton job={{ ...baseJob, apply_platform: 'lever' }} profile={completeProfile} onOpenModal={onOpenModal} />
      </MemoryRouter>
    )
    fireEvent.click(screen.getByRole('button', { name: /Smart Apply/i }))
    expect(onOpenModal).toHaveBeenCalledWith({ mode: 'hand_paste' })
  })

  it('eligible job with null apply_platform → onOpenModal receives mode=hand_paste', () => {
    const onOpenModal = vi.fn()
    render(
      <MemoryRouter>
        <AutoApplyButton job={{ ...baseJob, apply_platform: null }} profile={completeProfile} onOpenModal={onOpenModal} />
      </MemoryRouter>
    )
    fireEvent.click(screen.getByRole('button', { name: /Smart Apply/i }))
    expect(onOpenModal).toHaveBeenCalledWith({ mode: 'hand_paste' })
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

  it('accepts job.job_id as a fallback when job.id is absent', () => {
    // Callers (e.g. JobWorkspace) used to pre-bridge the prop with a spread.
    // Now apply components normalize internally — telemetry must receive the
    // correct id whether the caller passed `id` or `job_id`.
    render(
      <MemoryRouter>
        <AutoApplyButton
          job={{ ...baseJob, id: undefined, job_id: 'j-via-snake' }}
          profile={{ profile_complete: false }}
          onOpenModal={vi.fn()}
        />
      </MemoryRouter>
    )
    fireEvent.click(screen.getByRole('button', { name: /Complete profile to apply/i }))
    expect(t.ineligibleActionTaken).toHaveBeenCalledWith({ job_id: 'j-via-snake', reason: 'profile_incomplete' })
  })
})
