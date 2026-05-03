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

  it('renders human-readable labels (mapped + humanized fallback)', () => {
    render(<ProfileSnapshot snapshot={{ ...SNAP, custom_extension_field: 'foo' }} />)
    fireEvent.click(screen.getByRole('button', { name: /Profile snapshot/i }))

    // Mapped: snake_case → curated label
    expect(screen.getByText('Salary expectations')).toBeInTheDocument()
    expect(screen.getByText('Notice period')).toBeInTheDocument()
    expect(screen.getByText('Visa status')).toBeInTheDocument()
    // Fallback: unknown key → underscores stripped + capitalized
    expect(screen.getByText('Custom extension field')).toBeInTheDocument()
    // Raw snake_case must NOT leak through
    expect(screen.queryByText('salary_expectation_notes')).not.toBeInTheDocument()
  })
})
