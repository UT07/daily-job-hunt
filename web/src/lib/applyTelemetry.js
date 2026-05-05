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
