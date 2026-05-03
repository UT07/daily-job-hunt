import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { EmptyPreviewState } from '../EmptyPreviewState'

describe('EmptyPreviewState', () => {
  it('renders message and calls onRetry on click', () => {
    const onRetry = vi.fn()
    render(<EmptyPreviewState onRetry={onRetry} />)

    expect(screen.getByText(/AI prefill not available/i)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Retry preview/i }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })
})
