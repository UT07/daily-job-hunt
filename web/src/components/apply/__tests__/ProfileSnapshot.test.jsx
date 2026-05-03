import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ProfileSnapshot } from '../ProfileSnapshot'

const SNAP = {
  full_name: 'Daisy', visa_status: 'EU citizen',
  salary_expectation_notes: '€85k', notice_period_text: '4 weeks',
}

describe('ProfileSnapshot', () => {
  it('starts collapsed, expands on click', () => {
    render(<ProfileSnapshot snapshot={SNAP} />)
    expect(screen.queryByText('€85k')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Profile snapshot/i }))
    expect(screen.getByText('€85k')).toBeInTheDocument()
  })
})
