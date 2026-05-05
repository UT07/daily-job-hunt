import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { BrowserSessionView } from '../BrowserSessionView'

// Mock the hook — component-level test cares about render flow + reverse channel
const mockSendAction = vi.fn()
const mockDispose = vi.fn()
let hookValue
vi.mock('../../../hooks/useBrowserSession', () => ({
  useBrowserSession: () => hookValue,
}))

beforeEach(() => {
  vi.clearAllMocks()
  hookValue = {
    status: 'connecting',
    screenshotUrl: null,
    fields: [],
    error: null,
    sendAction: mockSendAction,
    dispose: mockDispose,
  }
})

const baseProps = {
  wsUrl: 'wss://api.test/prod',
  sessionId: 's1',
  token: 'tok',
  preview: { custom_questions: [{ id: 'why', label: 'Why?', ai_answer: 'Because.' }] },
  onSubmitted: vi.fn(),
}

describe('BrowserSessionView', () => {
  it('shows connecting state before any screenshot', () => {
    render(<BrowserSessionView {...baseProps} />)
    expect(screen.getByText(/Connecting…/i)).toBeInTheDocument()
    expect(screen.queryByRole('img', { name: /browser stream/i })).not.toBeInTheDocument()
  })

  it('renders the screenshot when one arrives', () => {
    hookValue = { ...hookValue, status: 'ready', screenshotUrl: 'data:image/jpeg;base64,/9j/AA' }
    render(<BrowserSessionView {...baseProps} />)
    const img = screen.getByRole('img', { name: /browser stream/i })
    expect(img.src).toBe('data:image/jpeg;base64,/9j/AA')
  })

  it('"Fill all" button sends fill_all with the preview answers', () => {
    hookValue = { ...hookValue, status: 'ready', screenshotUrl: 'data:x' }
    render(<BrowserSessionView {...baseProps} />)
    fireEvent.click(screen.getByRole('button', { name: /Fill all/i }))
    expect(mockSendAction).toHaveBeenCalledWith({
      action: 'fill_all',
      answers: { why: 'Because.' },
    })
  })

  it('disables "Fill all" when status is not ready', () => {
    hookValue = { ...hookValue, status: 'filling' }
    render(<BrowserSessionView {...baseProps} />)
    expect(screen.getByRole('button', { name: /Fill all/i })).toBeDisabled()
  })

  it('"End session" button calls dispose', () => {
    hookValue = { ...hookValue, status: 'ready', screenshotUrl: 'data:x' }
    render(<BrowserSessionView {...baseProps} />)
    fireEvent.click(screen.getByRole('button', { name: /End session/i }))
    expect(mockDispose).toHaveBeenCalled()
  })

  it('shows the captcha banner with a "Solving…" hint when status is captcha', () => {
    hookValue = { ...hookValue, status: 'captcha', screenshotUrl: 'data:x' }
    render(<BrowserSessionView {...baseProps} />)
    expect(screen.getByText(/Captcha detected/i)).toBeInTheDocument()
    expect(screen.getByText(/Solving/i)).toBeInTheDocument()
  })

  it('"Manual click" mode lets user click on the screenshot to send a click action', () => {
    hookValue = { ...hookValue, status: 'ready', screenshotUrl: 'data:x' }
    render(<BrowserSessionView {...baseProps} />)
    fireEvent.click(screen.getByRole('button', { name: /Manual click/i }))
    // Click on the stream image at coords (123, 456)
    const img = screen.getByRole('img', { name: /browser stream/i })
    fireEvent.click(img, { clientX: 123, clientY: 456 })
    expect(mockSendAction).toHaveBeenCalledWith(expect.objectContaining({
      action: 'click',
      x: expect.any(Number),
      y: expect.any(Number),
    }))
  })

  it('calls onSubmitted prop when status transitions to submitted', async () => {
    hookValue = { ...hookValue, status: 'ready' }
    const { rerender } = render(<BrowserSessionView {...baseProps} />)
    hookValue = { ...hookValue, status: 'submitted' }
    rerender(<BrowserSessionView {...baseProps} />)
    await waitFor(() => expect(baseProps.onSubmitted).toHaveBeenCalled())
  })
})
