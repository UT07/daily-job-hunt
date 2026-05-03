import { useState } from 'react'

const PROFILE_FIELD_LABELS = {
  full_name: 'Full name',
  first_name: 'First name',
  last_name: 'Last name',
  email: 'Email',
  phone: 'Phone',
  location: 'Location',
  linkedin: 'LinkedIn',
  github: 'GitHub',
  website: 'Website',
  visa_status: 'Visa status',
  work_authorization: 'Work authorization',
  salary_expectation_notes: 'Salary expectations',
  notice_period_text: 'Notice period',
  years_of_experience: 'Years of experience',
}

const humanizeKey = (k) =>
  k.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())

export function ProfileSnapshot({ snapshot }) {
  const [open, setOpen] = useState(false)
  if (!snapshot) return null

  const rows = Object.entries(snapshot).filter(([, v]) => v != null && v !== '')

  return (
    <div className="border-2 border-black">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full p-2 text-left font-mono hover:bg-yellow-100"
      >
        {open ? '▾' : '▸'} Profile snapshot
      </button>
      {open && (
        <dl className="p-2 font-mono text-sm">
          {rows.map(([k, v]) => (
            <div key={k} className="flex gap-2 py-1">
              <dt className="font-bold w-48">{PROFILE_FIELD_LABELS[k] || humanizeKey(k)}</dt>
              <dd>{String(v)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}
