import posthog from 'posthog-js'

// All wrappers no-op silently if posthog isn't initialized (key missing in dev).
function capture(event, props) {
  try {
    posthog.capture(event, props)
  } catch {
    // posthog-js no-ops when uninitialized; this catch is for the test mock case.
  }
}

export const modalOpened       = (props) => capture('apply_modal_opened', props)
export const fieldCopied       = (props) => capture('apply_field_copied', props)
export const atsOpened         = (props) => capture('apply_ats_opened', props)
export const markedApplied     = (props) => capture('apply_marked_applied', props)
export const modalDismissed    = (props) => capture('apply_modal_dismissed', props)
export const ineligibleActionTaken = (props) => capture('apply_ineligible_action_taken', props)

// Session-level events for Plan 3c cloud-browser auto-apply
const _captchaSeen = new Set() // session_id → boolean — for idempotent fire

export const sessionStarted = ({ job_id, session_id, reused }) =>
  capture('apply_session_started', { job_id, session_id, reused })

export const sessionReconnected = ({ session_id, attempt }) =>
  capture('apply_session_reconnected', { session_id, attempt })

export const captchaDetected = ({ session_id, type }) => {
  if (_captchaSeen.has(session_id)) return
  _captchaSeen.add(session_id)
  capture('apply_captcha_detected', { session_id, type })
}

export const fillAllSent = ({ session_id, answer_count }) =>
  capture('apply_fill_all_sent', { session_id, answer_count })

export const submittedReceived = ({ session_id }) =>
  capture('apply_submitted_received', { session_id })

export const sessionFailed = ({ job_id, error }) =>
  capture('apply_session_failed', { job_id, error })
