const TOOLTIPS = {
  eligible: 'Smart Apply available',
  profile_incomplete: 'Profile incomplete — finish in Settings',
  no_resume: 'No tailored resume yet',
  no_apply_url: 'No apply URL on this job',
  already_applied: 'Already applied',
}

const STATE_BY_REASON = {
  eligible: 'eligible',
  profile_incomplete: 'recoverable',
  no_resume: 'recoverable',
  no_apply_url: 'recoverable',
  already_applied: 'terminal',
}

const COLOR_CLASS = {
  eligible: 'bg-green-500',
  recoverable: 'bg-amber-400',
  terminal: 'bg-gray-400',
}

export function EligibilityBadge({ eligible, reason, platform }) {
  const key = eligible ? 'eligible' : reason
  const state = STATE_BY_REASON[key] ?? 'terminal'
  const tooltip = TOOLTIPS[key] ?? 'Eligibility unknown'

  return (
    <span
      data-testid="eligibility-badge"
      data-state={state}
      data-platform={platform || ''}
      title={tooltip}
      className={`inline-block w-2.5 h-2.5 rounded-full ${COLOR_CLASS[state]}`}
      aria-label={tooltip}
    />
  )
}
