import { Link } from 'react-router-dom'
import { useUserProfile } from '../hooks/useUserProfile'

// Mirrors the backend rule, shared/profile_completeness.py REQUIRED_FIELDS:
//   first_name, last_name, email, phone, linkedin, visa_status,
//   work_authorizations, notice_period_text
// in the FRONTEND shape ProfileResponse returns:
//   - linkedin → linkedin_url; the rest pass through
//   - first_name/last_name are not returned. app.py's PUT /api/profile derives
//     them from full_name as `v.strip().split(" ", 1)`, last_name being the
//     stripped remainder or '' -- so a one-word name leaves last_name empty
//     and the profile incomplete. That case is checked by `nameProblem`, not
//     by presence: it used to pass here and the banner listed nothing.
const REQUIRED_FIELDS = [
  { key: 'email',               label: 'Email' },
  { key: 'phone',               label: 'Phone' },
  { key: 'linkedin_url',        label: 'LinkedIn URL' },
  { key: 'visa_status',         label: 'Visa status' },
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

// 'missing' | 'one-word' | null, by app.py's split rule above.
function nameProblem(fullName) {
  const name = (fullName || '').trim()
  if (!name) return 'missing'
  const space = name.indexOf(' ')
  if (space < 0 || !name.slice(space + 1).trim()) return 'one-word'
  return null
}

export default function FinishSetupBanner() {
  // useUserProfile returns { profile, isLoading, refetch }; NOT { data, ... }
  const { profile } = useUserProfile()
  const name = nameProblem(profile?.full_name)
  const missing = [
    ...(name === 'missing' ? ['Full name'] : []),
    ...REQUIRED_FIELDS.filter(({ key }) => isMissing(profile?.[key])).map((f) => f.label),
  ]

  return (
    <div className="bg-yellow border-b-2 border-black px-4 py-3 flex items-center justify-between gap-4">
      <div className="text-sm">
        <span className="font-bold">Your profile is incomplete.</span>{' '}
        {missing.length > 0 && (
          <>
            Missing:{' '}
            <span className="font-mono">{missing.join(', ')}</span>.{' '}
          </>
        )}
        {name === 'one-word' && 'Full name needs a first and last name. '}
        {/* Reached only if the backend says incomplete for a reason this
            mirror does not know (e.g. a name written by résumé upload, which
            does not derive first/last). Point at Settings, where re-saving
            the name fixes that case. */}
        {missing.length === 0 && name !== 'one-word' &&
          'Re-save your profile in Settings to finish setup.'}
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
