import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SessionStatusBadge } from '../SessionStatusBadge'

describe('SessionStatusBadge', () => {
  const cases = [
    ['connecting',   'Connecting…',  'amber'],
    ['connected',    'Connected',    'amber'],
    ['ready',        'Ready',        'green'],
    ['filling',      'Filling form', 'blue'],
    ['captcha',      'Captcha',      'amber'],
    ['submitted',    'Submitted',    'green'],
    ['reconnecting', 'Reconnecting', 'amber'],
    ['error',        'Error',        'red'],
    ['closed',       'Closed',       'stone'],
  ]
  cases.forEach(([status, label, color]) => {
    it(`renders ${label} with ${color} accent for status="${status}"`, () => {
      render(<SessionStatusBadge status={status} />)
      const el = screen.getByTestId('session-status-badge')
      expect(el.textContent).toContain(label)
      expect(el.className).toContain(color)
    })
  })

  it('renders the unknown status as plain text without crashing', () => {
    render(<SessionStatusBadge status="some_unknown_status" />)
    expect(screen.getByTestId('session-status-badge').textContent).toContain('some_unknown_status')
  })
})
