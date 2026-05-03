import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { AutoApplyModal } from '../AutoApplyModal'

vi.mock('../../../api', () => ({ apiGet: vi.fn(), apiCall: vi.fn() }))
vi.mock('../../../lib/applyTelemetry', () => ({
  modalOpened: vi.fn(),
  modalDismissed: vi.fn(),
  fieldCopied: vi.fn(),
  atsOpened: vi.fn(),
  markedApplied: vi.fn(),
}))
import { apiGet, apiCall } from '../../../api'
import * as t from '../../../lib/applyTelemetry'

const job = { id: 'j1', title: 'SRE', company: 'Acme', apply_url: 'https://acme.com/apply', apply_platform: 'greenhouse' }
// Match the actual /api/apply/preview shape (app.py:2896-2927).
const previewPayload = {
  eligible: true,
  job: { title: 'SRE', company: 'Acme', apply_url: 'https://acme.com/apply' },
  resume: { s3_url: 'https://r.s3', filename: 'resume.pdf', resume_version: 1, s3_key: 'k', is_default: false },
  cover_letter: { text: 'Dear hiring team,\nI am writing about your SRE role...', editable: true, max_length: 10000, source: 'ai_generated', include_by_default: true },
  custom_questions: [{ id: 'why', label: 'Why?', type: 'textarea', required: true, ai_answer: 'Because.', requires_user_action: false, category: 'custom' }],
  profile: { first_name: 'Daisy', last_name: 'X', email: 'd@x.io', phone: '+353', linkedin: 'in/daisy', github: 'gh/daisy', website: '', location: 'Dublin' },
  platform: 'greenhouse',
}

describe('AutoApplyModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiGet.mockResolvedValue(previewPayload)
    apiCall.mockResolvedValue({ ok: true })
  })

  it('opens, fetches preview, fires modalOpened telemetry', async () => {
    render(<AutoApplyModal job={job} isOpen onClose={vi.fn()} onMarkApplied={vi.fn()} />)
    expect(t.modalOpened).toHaveBeenCalledWith({ job_id: 'j1', platform: 'greenhouse', reason: 'eligible' })
    await waitFor(() => expect(apiGet).toHaveBeenCalledWith('/api/apply/preview/j1'))
  })

  it('shows EmptyPreviewState when custom_questions is empty', async () => {
    apiGet.mockResolvedValueOnce({ ...previewPayload, custom_questions: [] })
    render(<AutoApplyModal job={job} isOpen onClose={vi.fn()} onMarkApplied={vi.fn()} />)
    await waitFor(() => expect(screen.getByText(/AI prefill not available/i)).toBeInTheDocument())
  })

  it('Open ATS swaps primary to "I submitted — mark applied" + fires atsOpened', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    render(<AutoApplyModal job={job} isOpen onClose={vi.fn()} onMarkApplied={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('button', { name: /Open ATS/i })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /Open ATS/i }))
    expect(openSpy).toHaveBeenCalledWith('https://acme.com/apply', '_blank')
    expect(t.atsOpened).toHaveBeenCalled()
    expect(screen.getByRole('button', { name: /I submitted/i })).toBeInTheDocument()
    openSpy.mockRestore()
  })

  it('Mark applied calls /api/apply/record then onMarkApplied + onClose', async () => {
    const onMarkApplied = vi.fn()
    const onClose = vi.fn()
    render(<AutoApplyModal job={job} isOpen onClose={onClose} onMarkApplied={onMarkApplied} />)
    await waitFor(() => screen.getByRole('button', { name: /Open ATS/i }))
    vi.spyOn(window, 'open').mockImplementation(() => null)
    fireEvent.click(screen.getByRole('button', { name: /Open ATS/i }))
    fireEvent.click(screen.getByRole('button', { name: /I submitted/i }))
    await waitFor(() => expect(apiCall).toHaveBeenCalledWith('/api/apply/record', expect.objectContaining({ job_id: 'j1' })))
    await waitFor(() => expect(onMarkApplied).toHaveBeenCalledTimes(1))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('Mark applied failure keeps modal open + does not call onMarkApplied', async () => {
    apiCall.mockRejectedValueOnce(new Error('500 server'))
    const onMarkApplied = vi.fn()
    const onClose = vi.fn()
    render(<AutoApplyModal job={job} isOpen onClose={onClose} onMarkApplied={onMarkApplied} />)
    await waitFor(() => screen.getByRole('button', { name: /Open ATS/i }))
    vi.spyOn(window, 'open').mockImplementation(() => null)
    fireEvent.click(screen.getByRole('button', { name: /Open ATS/i }))
    fireEvent.click(screen.getByRole('button', { name: /I submitted/i }))
    await waitFor(() => expect(apiCall).toHaveBeenCalled())
    expect(onMarkApplied).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByText(/Couldn't mark applied/i)).toBeInTheDocument()
  })

  it('dismiss without marking applied fires modalDismissed', async () => {
    const onClose = vi.fn()
    const { rerender } = render(<AutoApplyModal job={job} isOpen onClose={onClose} onMarkApplied={vi.fn()} />)
    await waitFor(() => screen.getByRole('button', { name: /Open ATS/i }))
    rerender(<AutoApplyModal job={job} isOpen={false} onClose={onClose} onMarkApplied={vi.fn()} />)
    expect(t.modalDismissed).toHaveBeenCalled()
  })

  it('disables mark-applied button during in-flight POST', async () => {
    apiGet.mockResolvedValue(previewPayload)
    let resolveRecord
    apiCall.mockReturnValue(new Promise((resolve) => { resolveRecord = resolve }))

    render(<AutoApplyModal job={job} isOpen onClose={vi.fn()} onMarkApplied={vi.fn()} />)
    await waitFor(() => screen.getByRole('button', { name: /Open ATS/i }))

    // ATS-opened state is required to swap the button
    vi.spyOn(window, 'open').mockImplementation(() => null)
    fireEvent.click(screen.getByRole('button', { name: /Open ATS/i }))
    const markBtn = await screen.findByRole('button', { name: /I submitted/i })

    fireEvent.click(markBtn)
    await waitFor(() => expect(screen.getByRole('button', { name: /Recording/i })).toBeDisabled())

    resolveRecord({}) // unblock
    await waitFor(() => expect(apiCall).toHaveBeenCalledTimes(1))
  })
})
