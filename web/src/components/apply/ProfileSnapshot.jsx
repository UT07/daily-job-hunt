import { useState } from 'react'

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
              <dt className="font-bold w-48">{k}</dt>
              <dd>{String(v)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}
