/**
 * The notice-period dropdown must show the value the profile actually holds.
 *
 * Live E2E against production (2026-10-09): the profile stored "1 month" and
 * Settings showed "Select…". Settings and Onboarding both mount the picker
 * with value="" and fill it in when GET /api/profile resolves; the picker
 * computed its select mode once, in useState's initialiser, and never looked
 * at `value` again. These tests mount with "" and deliver the value after
 * mount, which is the order production uses.
 */
import { describe, it, expect } from 'vitest'
import { useState } from 'react'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { NoticePeriodPicker } from '../ui/NoticePeriodPicker'

// Owns the value the way Settings does: starts blank, parent may set it later.
let setFromServer
function Host({ initial = '' }) {
  const [value, setValue] = useState(initial)
  setFromServer = setValue
  return (
    <>
      <NoticePeriodPicker value={value} onChange={setValue} />
      <span data-testid="stored">{value}</span>
    </>
  )
}

const select = () => screen.getByTestId('notice-period-select')

describe('NoticePeriodPicker follows a value that arrives after mount', () => {
  it('shows a preset loaded after mount', () => {
    render(<Host />)
    expect(select()).toHaveValue('')
    act(() => setFromServer('1 month'))
    expect(select()).toHaveValue('1 month')
  })

  it('shows a non-preset value loaded after mount as Custom with its text', () => {
    render(<Host />)
    act(() => setFromServer('end of quarter'))
    expect(select()).toHaveValue('__custom__')
    expect(screen.getByTestId('notice-period-custom-input')).toHaveValue('end of quarter')
  })

  it('still opens an empty Custom field when the user picks Custom…', () => {
    render(<Host initial="1 month" />)
    fireEvent.change(select(), { target: { value: '__custom__' } })
    expect(select()).toHaveValue('__custom__')
    const input = screen.getByTestId('notice-period-custom-input')
    expect(input).toHaveValue('')
    expect(screen.getByTestId('stored')).toHaveTextContent('')
  })

  it('stays in Custom while the typed text happens to equal a preset', () => {
    render(<Host />)
    fireEvent.change(select(), { target: { value: '__custom__' } })
    fireEvent.change(screen.getByTestId('notice-period-custom-input'), { target: { value: '1 week' } })
    expect(select()).toHaveValue('__custom__')
    expect(screen.getByTestId('notice-period-custom-input')).toHaveValue('1 week')
  })

  it('choosing a preset after Custom leaves custom mode', () => {
    render(<Host initial="end of quarter" />)
    fireEvent.change(select(), { target: { value: '2 weeks' } })
    expect(select()).toHaveValue('2 weeks')
    expect(screen.queryByTestId('notice-period-custom-input')).not.toBeInTheDocument()
    expect(screen.getByTestId('stored')).toHaveTextContent('2 weeks')
  })
})
