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

describe('Plan 3c session telemetry', () => {
  beforeEach(() => vi.clearAllMocks())

  it('sessionStarted captures session_id + reused flag', () => {
    t.sessionStarted({ job_id: 'j1', session_id: 'sess-1', reused: false })
    expect(posthog.capture).toHaveBeenCalledWith('apply_session_started', {
      job_id: 'j1', session_id: 'sess-1', reused: false,
    })
  })

  it('sessionReconnected captures attempt count', () => {
    t.sessionReconnected({ session_id: 'sess-1', attempt: 2 })
    expect(posthog.capture).toHaveBeenCalledWith('apply_session_reconnected', {
      session_id: 'sess-1', attempt: 2,
    })
  })

  it('captchaDetected fires once per session (idempotent)', () => {
    t.captchaDetected({ session_id: 'sess-cap-1', type: 'hcaptcha' })
    t.captchaDetected({ session_id: 'sess-cap-1', type: 'hcaptcha' })
    expect(posthog.capture).toHaveBeenCalledTimes(1)
  })

  it('fillAllSent captures answer count', () => {
    t.fillAllSent({ session_id: 'sess-1', answer_count: 12 })
    expect(posthog.capture).toHaveBeenCalledWith('apply_fill_all_sent', {
      session_id: 'sess-1', answer_count: 12,
    })
  })

  it('submittedReceived fires when WS reports status=submitted', () => {
    t.submittedReceived({ session_id: 'sess-1' })
    expect(posthog.capture).toHaveBeenCalledWith('apply_submitted_received', {
      session_id: 'sess-1',
    })
  })

  it('sessionFailed captures the error reason', () => {
    t.sessionFailed({ job_id: 'j1', error: 'profile_incomplete:phone' })
    expect(posthog.capture).toHaveBeenCalledWith('apply_session_failed', {
      job_id: 'j1', error: 'profile_incomplete:phone',
    })
  })
})
