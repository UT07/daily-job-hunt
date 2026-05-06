import { Link } from 'react-router-dom'
import { useUserProfile } from '../hooks/useUserProfile'

// Backend `check_profile_completeness` requires these fields. Keep in sync
// with shared/profile_completeness.py REQUIRED_FIELDS.
const REQUIRED_FIELDS = [
  { key: 'first_name', label: 'First name' },
  { key: 'last_name',  label: 'Last name'  },
  { key: 'phone',      label: 'Phone' },
  { key: 'linkedin',   label: 'LinkedIn URL' },
  { key: 'visa_status', label: 'Visa status' },
  { key: 'work_authorizations', label: 'Work authorizations' },
  { key: 'notice_period_text',  label: 'Notice period' },
]

function isMissing(value) {
  if (value == null) return true
  if (typeof value === 'string') return !value.trim()
  if (Array.isArray(value)) return value.length === 0
  if (typeof value === 'object') return Object.keys(value).length === 0
  return false
}

export default function FinishSetupBanner() {
  const { data: profile } = useUserProfile()
  const missing = REQUIRED_FIELDS.filter(({ key }) => isMissing(profile?.[key]))

  return (
    <div className="bg-yellow border-b-2 border-black px-4 py-3 flex items-center justify-between gap-4">
      <div className="text-sm">
        <span className="font-bold">Your profile is incomplete.</span>{' '}
        {missing.length > 0 ? (
          <>
            Missing:{' '}
            <span className="font-mono">{missing.map((m) => m.label).join(', ')}</span>.
          </>
        ) : (
          'Complete setup to enable auto-apply.'
        )}
      </div>
      {/* Link to Settings (where every required field has UI), NOT /onboarding —
          users with onboarding_completed_at already set should fill the missing
          fields on Settings instead of being looped through the wizard. */}
      <Link to="/settings" className="text-sm font-bold underline hover:no-underline whitespace-nowrap">
        Open Settings →
      </Link>
    </div>
  )
}
