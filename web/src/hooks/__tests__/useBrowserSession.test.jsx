import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useBrowserSession } from '../useBrowserSession'

// Tiny WebSocket stand-in. Tests drive received frames + assert sent frames.
class MockSocket {
  constructor(url, protocols) {
    this.url = url
    this.protocols = protocols
    this.readyState = 0   // CONNECTING
    this.sent = []
    MockSocket.last = this
  }
  send(data) { this.sent.push(data) }
  close() { this.readyState = 3; this.onclose?.({ code: 1000 }) }
  // Test helpers
  _open() { this.readyState = 1; this.onopen?.() }
  _msg(data) { this.onmessage?.({ data }) }
  _err(err = new Error('boom')) { this.onerror?.(err) }
}

beforeEach(() => {
  vi.stubGlobal('WebSocket', MockSocket)
  MockSocket.last = null
})
afterEach(() => vi.unstubAllGlobals())

describe('useBrowserSession', () => {
  it('connects with subprotocol token + tracks status across status frames', async () => {
    const { result } = renderHook(() => useBrowserSession({
      wsUrl: 'wss://api.test/prod',
      sessionId: 'sess-1',
      token: 'tok-XYZ',
    }))

    // Initial state
    expect(result.current.status).toBe('connecting')
    // Constructor was called with subprotocol carrying the token
    expect(MockSocket.last.url).toContain('?session=sess-1&role=frontend')
    expect(MockSocket.last.protocols).toEqual(['naukribaba-auth.tok-XYZ'])

    act(() => MockSocket.last._open())
    await waitFor(() => expect(result.current.status).toBe('connected'))

    // Receive a "ready" status frame
    act(() => MockSocket.last._msg(JSON.stringify({ action: 'status', status: 'ready' })))
    await waitFor(() => expect(result.current.status).toBe('ready'))

    // Receive a "filling" with progress
    act(() => MockSocket.last._msg(JSON.stringify({ action: 'status', status: 'filling' })))
    await waitFor(() => expect(result.current.status).toBe('filling'))
  })

  it('decodes binary frames into a screenshot data URL', async () => {
    // Defensive coverage: if the transport ever delivers true binary frames
    // (currently API Gateway always coerces to Text), the legacy path still
    // works. Real prod traffic uses the FRAME envelope below.
    const { result } = renderHook(() => useBrowserSession({
      wsUrl: 'wss://api.test/prod', sessionId: 's', token: 't',
    }))
    act(() => MockSocket.last._open())

    // Build a tiny "JPEG" buffer
    const blob = new Uint8Array([0xff, 0xd8, 0xff]).buffer
    act(() => MockSocket.last._msg(blob))
    await waitFor(() => expect(result.current.screenshotUrl).toMatch(/^data:image\/jpeg;base64,/))
  })

  it('decodes FRAME JSON envelopes into a screenshot data URL', async () => {
    // The actual prod path: bot sends {action:'frame', jpeg:'<base64>'} as a
    // JSON text frame. Probe Layer #9 (scripts/probe_smart_apply.py) proves
    // API Gateway always delivers Text opcodes, so this is the canonical
    // delivery shape. base64('\xff\xd8\xff') === '/9j/'.
    const { result } = renderHook(() => useBrowserSession({
      wsUrl: 'wss://api.test/prod', sessionId: 's', token: 't',
    }))
    act(() => MockSocket.last._open())

    act(() => MockSocket.last._msg(JSON.stringify({ action: 'frame', jpeg: '/9j/' })))
    await waitFor(() =>
      expect(result.current.screenshotUrl).toBe('data:image/jpeg;base64,/9j/'),
    )
  })

  it('ignores FRAME envelopes whose jpeg field is missing or wrong type', async () => {
    // Defensive: a malformed bot must not crash the FE or replace a previous
    // valid screenshot with `data:image/jpeg;base64,undefined`.
    const { result } = renderHook(() => useBrowserSession({
      wsUrl: 'wss://api.test/prod', sessionId: 's', token: 't',
    }))
    act(() => MockSocket.last._open())

    // First, a valid frame establishes a known good state
    act(() => MockSocket.last._msg(JSON.stringify({ action: 'frame', jpeg: '/9j/' })))
    await waitFor(() => expect(result.current.screenshotUrl).toBe('data:image/jpeg;base64,/9j/'))

    // Malformed payloads — must be ignored
    act(() => MockSocket.last._msg(JSON.stringify({ action: 'frame' })))               // no jpeg field
    act(() => MockSocket.last._msg(JSON.stringify({ action: 'frame', jpeg: null })))   // null
    act(() => MockSocket.last._msg(JSON.stringify({ action: 'frame', jpeg: 42 })))     // wrong type

    // State is unchanged
    expect(result.current.screenshotUrl).toBe('data:image/jpeg;base64,/9j/')
  })

  it('sendAction stringifies + sends an outbound JSON frame', async () => {
    const { result } = renderHook(() => useBrowserSession({
      wsUrl: 'wss://api.test/prod', sessionId: 's', token: 't',
    }))
    act(() => MockSocket.last._open())
    await waitFor(() => expect(result.current.status).toBe('connected'))

    act(() => result.current.sendAction({ action: 'fill_all', answers: { a: 'b' } }))

    expect(MockSocket.last.sent).toHaveLength(1)
    expect(JSON.parse(MockSocket.last.sent[0])).toEqual({
      action: 'fill_all', answers: { a: 'b' },
    })
  })

  it('refuses to send before the socket has opened', () => {
    const { result } = renderHook(() => useBrowserSession({
      wsUrl: 'wss://api.test/prod', sessionId: 's', token: 't',
    }))
    // No _open() call → readyState is still CONNECTING

    act(() => result.current.sendAction({ action: 'submit' }))
    expect(MockSocket.last.sent).toHaveLength(0)
  })

  it('reconnects with backoff after unexpected close', async () => {
    vi.useFakeTimers()
    try {
      const { result } = renderHook(() => useBrowserSession({
        wsUrl: 'wss://api.test/prod', sessionId: 's', token: 't',
      }))
      act(() => MockSocket.last._open())

      const first = MockSocket.last
      // Unexpected close (code != 1000)
      act(() => first.onclose?.({ code: 1006 }))
      await waitFor(() => expect(result.current.status).toBe('reconnecting'))

      // Backoff is 1s, 2s, 4s — let the first elapse
      act(() => vi.advanceTimersByTime(1100))
      expect(MockSocket.last).not.toBe(first)
    } finally {
      vi.useRealTimers()
    }
  })

  it('disposes the socket on unmount', () => {
    const { unmount } = renderHook(() => useBrowserSession({
      wsUrl: 'wss://api.test/prod', sessionId: 's', token: 't',
    }))
    const sock = MockSocket.last
    const closeSpy = vi.spyOn(sock, 'close')
    unmount()
    expect(closeSpy).toHaveBeenCalled()
  })
})
