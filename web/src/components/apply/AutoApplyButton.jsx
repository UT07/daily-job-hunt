import { useNavigate } from 'react-router-dom'
import { computeEligibility } from '../../hooks/useApplyEligibility'
import { ineligibleActionTaken } from '../../lib/applyTelemetry'

const STATE_CONFIG = {
  eligible:           { label: 'Smart Apply',                       disabled: false },
  profile_incomplete: { label: 'Complete profile to apply',         disabled: false },
  no_resume:          { label: 'Generate tailored resume first',    disabled: false },
  no_apply_url:       { label: 'Add apply URL',                     disabled: false },
  already_applied:    { label: 'Applied ✓',                         disabled: true  },
}

export function AutoApplyButton({ job, profile, onOpenModal }) {
  const navigate = useNavigate()
  const jobId = job.id || job.job_id
  const eligibility = computeEligibility(job, profile)
  const stateKey = eligibility.eligible ? 'eligible' : eligibility.reason
  const cfg = STATE_CONFIG[stateKey]

  const platformSupportsCloudBrowser = ['greenhouse', 'ashby'].includes(job.apply_platform)
  const mode = (eligibility.eligible && platformSupportsCloudBrowser) ? 'cloud_browser' : 'hand_paste'

  const onClick = () => {
    if (stateKey === 'eligible') {
      onOpenModal({ mode })
      return
    }
    ineligibleActionTaken({ job_id: jobId, reason: stateKey })
    if (stateKey === 'profile_incomplete') {
      navigate('/settings#profile')
    } else if (stateKey === 'no_resume') {
      const tailorCard = document.querySelector('[data-testid="tailor-card"]')
      tailorCard?.scrollIntoView({ behavior: 'smooth' })
      tailorCard?.querySelector('button')?.focus()
    } else if (stateKey === 'no_apply_url') {
      // Wired to the JobWorkspace overview-tab "Edit" button. If the user
      // isn't on the overview tab (or is browsing from JobTable), the
      // affordance won't exist — telemetry has already fired above.
      const editField = document.querySelector('[data-testid="apply-url-edit"]')
      editField?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      editField?.click()
    }
  }

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={cfg.disabled}
      data-testid="auto-apply-button"
      data-state={stateKey}
      className="px-4 py-2 border-2 border-black bg-yellow-300 hover:bg-yellow-400 disabled:bg-gray-200 disabled:cursor-not-allowed font-mono"
    >
      {cfg.label}
    </button>
  )
}
