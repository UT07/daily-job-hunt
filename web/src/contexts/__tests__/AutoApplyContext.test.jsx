import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { AutoApplyProvider, useAutoApply } from '../AutoApplyContext'

function Probe() {
  const { activeSessionId, activeJobId, beginSession, endSession } = useAutoApply()
  const [error, setError] = React.useState(null)

  const safeBeginSession = (args) => {
    try {
      beginSession(args)
    } catch (e) {
      setError(e.message)
      // Don't re-throw — caller only cares that error is caught and displayed
    }
  }

  return (
    <div>
      <span data-testid="state">{activeJobId ?? 'none'}/{activeSessionId ?? 'none'}</span>
      {error && <span data-testid="error">{error}</span>}
      <button onClick={() => safeBeginSession({ sessionId: 's1', jobId: 'j1' })}>begin1</button>
      <button onClick={() => safeBeginSession({ sessionId: 's2', jobId: 'j2' })}>begin2</button>
      <button onClick={endSession}>end</button>
    </div>
  )
}

describe('AutoApplyContext', () => {
  it('starts with no active session', () => {
    render(<AutoApplyProvider><Probe /></AutoApplyProvider>)
    expect(screen.getByTestId('state').textContent).toBe('none/none')
  })

  it('beginSession sets active job + session', () => {
    render(<AutoApplyProvider><Probe /></AutoApplyProvider>)
    fireEvent.click(screen.getByText('begin1'))
    expect(screen.getByTestId('state').textContent).toBe('j1/s1')
  })

  it('endSession clears active state', () => {
    render(<AutoApplyProvider><Probe /></AutoApplyProvider>)
    fireEvent.click(screen.getByText('begin1'))
    fireEvent.click(screen.getByText('end'))
    expect(screen.getByTestId('state').textContent).toBe('none/none')
  })

  it('beginSession on a 2nd job throws if one is already active', () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    render(<AutoApplyProvider><Probe /></AutoApplyProvider>)
    fireEvent.click(screen.getByText('begin1'))
    // beginSession should refuse to silently overwrite — caller must endSession first
    // Wrap in expect to catch the error thrown by safeBeginSession
    expect(() => {
      fireEvent.click(screen.getByText('begin2'))
    }).not.toThrow()
    // The error was caught by safeBeginSession and stored in state
    expect(screen.getByTestId('error').textContent).toBe('session_already_active')
    consoleSpy.mockRestore()
  })

  it('useAutoApply throws outside the provider', () => {
    // Suppress React error boundary noise
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    expect(() => render(<Probe />)).toThrow(/AutoApplyProvider/)
    consoleSpy.mockRestore()
  })
})
