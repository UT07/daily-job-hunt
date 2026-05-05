import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('posthog-js', () => ({
  default: { capture: vi.fn() },
}))
import posthog from 'posthog-js'
import * as t from '../applyTelemetry'

describe('applyTelemetry', () => {
  beforeEach(() => vi.clearAllMocks())

  it('modalOpened captures job_id, platform, reason', () => {
    t.modalOpened({ job_id: 'j1', platform: 'greenhouse', reason: 'eligible' })
    expect(posthog.capture).toHaveBeenCalledWith('apply_modal_opened', {
      job_id: 'j1', platform: 'greenhouse', reason: 'eligible',
    })
  })

  it('fieldCopied captures job_id + field_name', () => {
    t.fieldCopied({ job_id: 'j1', field_name: 'why_interested' })
    expect(posthog.capture).toHaveBeenCalledWith('apply_field_copied', {
      job_id: 'j1', field_name: 'why_interested',
    })
  })

  it('atsOpened captures job_id + platform', () => {
    t.atsOpened({ job_id: 'j1', platform: 'greenhouse' })
    expect(posthog.capture).toHaveBeenCalledWith('apply_ats_opened', {
      job_id: 'j1', platform: 'greenhouse',
    })
  })

  it('markedApplied captures job_id, platform, ats_was_opened', () => {
    t.markedApplied({ job_id: 'j1', platform: 'greenhouse', ats_was_opened: true })
    expect(posthog.capture).toHaveBeenCalledWith('apply_marked_applied', {
      job_id: 'j1', platform: 'greenhouse', ats_was_opened: true,
    })
  })

  it('modalDismissed captures job_id, platform, ats_was_opened', () => {
    t.modalDismissed({ job_id: 'j1', platform: 'greenhouse', ats_was_opened: false })
    expect(posthog.capture).toHaveBeenCalledWith('apply_modal_dismissed', {
      job_id: 'j1', platform: 'greenhouse', ats_was_opened: false,
    })
  })

  it('ineligibleActionTaken captures job_id + reason', () => {
    t.ineligibleActionTaken({ job_id: 'j1', reason: 'profile_incomplete' })
    expect(posthog.capture).toHaveBeenCalledWith('apply_ineligible_action_taken', {
      job_id: 'j1', reason: 'profile_incomplete',
    })
  })
})
