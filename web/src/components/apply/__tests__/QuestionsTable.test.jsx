import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QuestionsTable } from '../QuestionsTable'

// Match the actual /api/apply/preview shape (app.py:2884-2894):
// { id, label, type, required, options, max_length, ai_answer, requires_user_action, category }
const Q = [
  { id: 'why_interested', label: 'Why are you interested?', ai_answer: 'Because I love payments.', required: true, requires_user_action: false, category: 'custom' },
  { id: 'experience_years', label: 'Years of experience?', ai_answer: '5+ years', required: true, requires_user_action: false, category: 'profile' },
  { id: 'salary_neg', label: 'Salary expectation?', ai_answer: null, required: false, requires_user_action: true, category: 'custom' },
]

describe('QuestionsTable', () => {
  it('writes the ai_answer to clipboard on copy click and calls onCopy with label', async () => {
    const onCopy = vi.fn()
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })

    render(<QuestionsTable questions={Q} onCopy={onCopy} />)

    const buttons = screen.getAllByRole('button', { name: /copy/i })
    fireEvent.click(buttons[0])

    await waitFor(() => expect(onCopy).toHaveBeenCalledWith({ field_name: 'Why are you interested?' }))
    expect(writeText).toHaveBeenCalledWith('Because I love payments.')
  })

  it('fires onCopy with error when clipboard.writeText rejects', async () => {
    const onCopy = vi.fn()
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('Denied')) } })

    render(<QuestionsTable questions={Q} onCopy={onCopy} />)
    fireEvent.click(screen.getAllByRole('button', { name: /copy/i })[0])

    await waitFor(() => expect(onCopy).toHaveBeenCalledWith(
      expect.objectContaining({ field_name: 'Why are you interested?', error: expect.stringContaining('Denied') })
    ))
  })

  it('disables copy when ai_answer is null (AI failed for that question)', () => {
    render(<QuestionsTable questions={Q} onCopy={vi.fn()} />)
    const buttons = screen.getAllByRole('button', { name: /copy/i })
    // 3rd row has ai_answer: null
    expect(buttons[2]).toBeDisabled()
  })

  it('shows fallback text in answer column when ai_answer is null', () => {
    render(<QuestionsTable questions={Q} onCopy={vi.fn()} />)
    expect(screen.getByText(/no AI answer/i)).toBeInTheDocument()
  })
})
