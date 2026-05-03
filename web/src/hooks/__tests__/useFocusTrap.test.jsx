import { describe, it, expect } from 'vitest'
import { useState } from 'react'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { useFocusTrap } from '../useFocusTrap'

function TrapHarness({ active }) {
  const ref = useFocusTrap(active)
  return (
    <div>
      <button data-testid="outside-before">outside-before</button>
      <div ref={ref} tabIndex={-1} data-testid="trap">
        <button data-testid="first">first</button>
        <button data-testid="middle">middle</button>
        <button data-testid="last">last</button>
      </div>
      <button data-testid="outside-after">outside-after</button>
    </div>
  )
}

function ToggleHarness() {
  const [active, setActive] = useState(false)
  return (
    <>
      <button data-testid="opener" onClick={() => setActive(true)}>open</button>
      <button data-testid="closer" onClick={() => setActive(false)}>close</button>
      {active && <TrapHarness active />}
    </>
  )
}

describe('useFocusTrap', () => {
  it('focuses the container on mount when nothing inside is focused', () => {
    render(<TrapHarness active />)
    expect(document.activeElement).toBe(screen.getByTestId('trap'))
  })

  it('Tab on the last focusable wraps to the first', () => {
    render(<TrapHarness active />)
    const last = screen.getByTestId('last')
    last.focus()
    expect(document.activeElement).toBe(last)

    fireEvent.keyDown(document, { key: 'Tab' })
    expect(document.activeElement).toBe(screen.getByTestId('first'))
  })

  it('Shift+Tab on the first focusable wraps to the last', () => {
    render(<TrapHarness active />)
    const first = screen.getByTestId('first')
    first.focus()

    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(screen.getByTestId('last'))
  })

  it('does not interfere with Tab on a middle focusable (browser default)', () => {
    render(<TrapHarness active />)
    const middle = screen.getByTestId('middle')
    middle.focus()

    // Default Tab behavior is delegated to the browser; the hook only
    // intercepts on first/last boundaries. Browsers / jsdom don't move
    // focus on synthetic Tab keydown, so the assertion is that the hook
    // did NOT call preventDefault / reassign focus.
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(document.activeElement).toBe(middle)
  })

  it('does nothing when inactive', () => {
    const outside = document.createElement('button')
    document.body.appendChild(outside)
    outside.focus()

    render(<TrapHarness active={false} />)
    // Inactive trap should NOT steal focus on mount.
    expect(document.activeElement).toBe(outside)
    document.body.removeChild(outside)
  })

  it('restores focus to the previously-focused element when deactivated', () => {
    render(<ToggleHarness />)
    const opener = screen.getByTestId('opener')
    opener.focus()
    expect(document.activeElement).toBe(opener)

    act(() => fireEvent.click(opener))
    // Trap is now active and stole focus to the container.
    expect(document.activeElement).toBe(screen.getByTestId('trap'))

    act(() => fireEvent.click(screen.getByTestId('closer')))
    // After unmount, focus returns to the opener.
    expect(document.activeElement).toBe(opener)
  })
})
