import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { AutoApplyModal } from '../AutoApplyModal'
import { AutoApplyProvider } from '../../../contexts/AutoApplyContext'

vi.mock('../../../api', () => ({ apiGet: vi.fn(), apiCall: vi.fn(), startApplySession: vi.fn(), stopApplySession: vi.fn() }))
vi.mock('../../../lib/applyTelemetry', () => ({
  modalOpened: vi.fn(),
  modalDismissed: vi.fn(),
  fieldCopied: vi.fn(),
  atsOpened: vi.fn(),
  markedApplied: vi.fn(),
  sessionStarted: vi.fn(),
  sessionFailed: vi.fn(),
  fillAllSent: vi.fn(),
}))
import { apiGet, apiCall, startApplySession } from '../../../api'
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

// Helper to avoid repeating AutoApplyProvider in every render call
const renderModal = (props) =>
  render(<AutoApplyProvider><AutoApplyModal {...props} /></AutoApplyProvider>)

describe('AutoApplyModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiGet.mockResolvedValue(previewPayload)
    apiCall.mockResolvedValue({ ok: true })
  })

  it('opens, fetches preview, fires modalOpened telemetry', async () => {
    renderModal({ job, isOpen: true, onClose: vi.fn(), onMarkApplied: vi.fn() })
    expect(t.modalOpened).toHaveBeenCalledWith({ job_id: 'j1', platform: 'greenhouse', reason: 'eligible' })
    await waitFor(() => expect(apiGet).toHaveBeenCalledWith('/api/apply/preview/j1'))
  })

  it('shows EmptyPreviewState when custom_questions is empty', async () => {
    apiGet.mockResolvedValueOnce({ ...previewPayload, custom_questions: [] })
    renderModal({ job, isOpen: true, onClose: vi.fn(), onMarkApplied: vi.fn() })
    await waitFor(() => expect(screen.getByText(/AI prefill not available/i)).toBeInTheDocument())
  })

  it('Open ATS swaps primary to "I submitted — mark applied" + fires atsOpened', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    renderModal({ job, isOpen: true, onClose: vi.fn(), onMarkApplied: vi.fn() })
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
    renderModal({ job, isOpen: true, onClose, onMarkApplied })
    await waitFor(() => screen.getByRole('button', { name: /Open ATS/i }))
    vi.spyOn(window, 'open').mockImplementation(() => null)
    fireEvent.click(screen.getByRole('button', { name: /Open ATS/i }))
    fireEvent.click(screen.getByRole('button', { name: /I submitted/i }))
    await waitFor(() => expect(apiCall).toHaveBeenCalledWith('/api/apply/record', {
      job_id: 'j1',
      submission_method: 'hand_paste',
    }))
    await waitFor(() => expect(onMarkApplied).toHaveBeenCalledTimes(1))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('Mark applied failure keeps modal open + does not call onMarkApplied', async () => {
    apiCall.mockRejectedValueOnce(new Error('500 server'))
    const onMarkApplied = vi.fn()
    const onClose = vi.fn()
    renderModal({ job, isOpen: true, onClose, onMarkApplied })
    await waitFor(() => screen.getByRole('button', { name: /Open ATS/i }))
    vi.spyOn(window, 'open').mockImplementation(() => null)
    fireEvent.click(screen.getByRole('button', { name: /Open ATS/i }))
    fireEvent.click(screen.getByRole('button', { name: /I submitted/i }))
    await waitFor(() => expect(apiCall).toHaveBeenCalled())
    expect(onMarkApplied).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByText(/Couldn't mark applied/i)).toBeInTheDocument()
  })

  it('dismiss without marking applied fires modalDismissed with full payload', async () => {
    const onClose = vi.fn()
    const { rerender } = renderModal({ job, isOpen: true, onClose, onMarkApplied: vi.fn() })
    await waitFor(() => screen.getByRole('button', { name: /Open ATS/i }))
    rerender(<AutoApplyProvider><AutoApplyModal job={job} isOpen={false} onClose={onClose} onMarkApplied={vi.fn()} /></AutoApplyProvider>)
    expect(t.modalDismissed).toHaveBeenCalledWith(expect.objectContaining({
      job_id: 'j1',
      platform: 'greenhouse',
      ats_was_opened: false,
    }))
  })

  it('clicking the backdrop calls onClose once', async () => {
    const onClose = vi.fn()
    renderModal({ job, isOpen: true, onClose, onMarkApplied: vi.fn() })
    await waitFor(() => screen.getByRole('button', { name: /Open ATS/i }))

    // Backdrop is the <div className="fixed inset-0 ..."> wrapping the dialog panel
    const dialog = screen.getByRole('dialog')
    const backdrop = dialog.parentElement
    fireEvent.click(backdrop)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('focuses the dialog on open and traps Tab from the last action', async () => {
    renderModal({ job, isOpen: true, onClose: vi.fn(), onMarkApplied: vi.fn() })
    await waitFor(() => screen.getByRole('button', { name: /Open ATS/i }))

    // useFocusTrap focuses the dialog itself when nothing inside has focus
    expect(document.activeElement).toBe(screen.getByRole('dialog'))

    // Tab from the last focusable (Open ATS) wraps to the first focusable
    // inside the panel — which is the resume download link rendered above.
    const openAts = screen.getByRole('button', { name: /Open ATS/i })
    openAts.focus()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(document.activeElement).toBe(screen.getByRole('link', { name: /Tailored Resume/i }))
  })

  it('closes modal when user presses Escape', async () => {
    apiGet.mockResolvedValue(previewPayload)
    const onClose = vi.fn()
    renderModal({ job, isOpen: true, onClose, onMarkApplied: vi.fn() })
    await waitFor(() => screen.getByRole('button', { name: /Open ATS/i }))

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('disables mark-applied button during in-flight POST', async () => {
    apiGet.mockResolvedValue(previewPayload)
    let resolveRecord
    apiCall.mockReturnValue(new Promise((resolve) => { resolveRecord = resolve }))

    renderModal({ job, isOpen: true, onClose: vi.fn(), onMarkApplied: vi.fn() })
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

// New describe block at the bottom of the file
describe('AutoApplyModal — cloud_browser mode', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiGet.mockResolvedValue(previewPayload)
    apiCall.mockResolvedValue({ ok: true })
    startApplySession.mockResolvedValue({
      session_id: 'sess-1',
      ws_url: 'wss://api.test/prod',
      ws_token: 'tok-XYZ',
      status: 'starting',
      reused: false,
    })
  })

  it('starts a session and renders BrowserSessionView when mode=cloud_browser', async () => {
    renderModal({
      job,
      isOpen: true,
      mode: 'cloud_browser',
      onClose: vi.fn(),
      onMarkApplied: vi.fn(),
    })
    await waitFor(() => expect(startApplySession).toHaveBeenCalledWith('j1'))
    // BrowserSessionView renders the connecting state
    await waitFor(() => expect(screen.getByText(/Connecting to browser session/i)).toBeInTheDocument())
  })

  it('falls back to hand_paste UI when start-session returns 412 profile_incomplete', async () => {
    startApplySession.mockRejectedValue(new Error('profile_incomplete:phone'))
    renderModal({
      job,
      isOpen: true,
      mode: 'cloud_browser',
      onClose: vi.fn(),
      onMarkApplied: vi.fn(),
    })
    await waitFor(() => expect(screen.getByText(/Open ATS/i)).toBeInTheDocument())
  })
})
