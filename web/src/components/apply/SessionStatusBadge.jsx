const STATUS_META = {
  connecting:   { label: 'Connecting…',  color: 'amber' },
  connected:    { label: 'Connected',    color: 'amber' },
  ready:        { label: 'Ready',        color: 'green' },
  filling:      { label: 'Filling form', color: 'blue'  },
  captcha:      { label: 'Captcha',      color: 'amber' },
  submitted:    { label: 'Submitted',    color: 'green' },
  reconnecting: { label: 'Reconnecting', color: 'amber' },
  error:        { label: 'Error',        color: 'red'   },
  closed:       { label: 'Closed',       color: 'stone' },
}

const COLOR_CLASSES = {
  amber: 'border-amber-500 bg-amber-100 text-amber-900',
  blue:  'border-blue-500 bg-blue-100 text-blue-900',
  green: 'border-green-600 bg-green-100 text-green-900',
  red:   'border-red-600 bg-red-100 text-red-900',
  stone: 'border-stone-500 bg-stone-100 text-stone-900',
}

export function SessionStatusBadge({ status }) {
  const meta = STATUS_META[status]
  const label = meta?.label ?? status
  const color = meta?.color ?? 'stone'
  return (
    <span
      data-testid="session-status-badge"
      className={`inline-block px-2 py-1 border-2 font-mono text-xs ${COLOR_CLASSES[color]}`}
    >
      {label}
    </span>
  )
}
