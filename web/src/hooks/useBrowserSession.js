import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ACTIONS_IN,
  parseTextFrame,
  binaryFrameToDataUrl,
} from '../lib/wsProtocol'
import {
  sessionReconnected,
  submittedReceived,
  captchaDetected,
} from '../lib/applyTelemetry'

const SUBPROTOCOL_PREFIX = 'naukribaba-auth.'

// Reconnect backoff: 1s, 2s, 4s, 8s — capped at 8s. Five attempts total.
const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 8000]

/**
 * Manage a Smart Apply WebSocket session. Returns:
 *   - status: 'connecting' | 'connected' | 'ready' | 'filling' | 'captcha' |
 *             'submitted' | 'reconnecting' | 'closed' | 'error'
 *   - screenshotUrl: data: URL of the latest JPEG frame (null until first frame)
 *   - fields: array of detected form fields (from inbound 'fields' frame)
 *   - sendAction(payload): send an outbound JSON frame; no-op if not OPEN
 *   - dispose(): force-close the socket (cleanup is automatic on unmount)
 */
export function useBrowserSession({ wsUrl, sessionId, token, onSubmitted }) {
  const [status, setStatus] = useState('connecting')
  const [screenshotUrl, setScreenshotUrl] = useState(null)
  const [fields, setFields] = useState([])
  const [error, setError] = useState(null)

  const socketRef = useRef(null)
  const reconnectAttemptRef = useRef(0)
  const reconnectTimerRef = useRef(null)
  const disposedRef = useRef(false)
  // Throttle screenshot updates: store the latest pending frame and schedule
  // a single rAF flush. At Fargate's 5-10 fps, multiple frames may queue
  // within one animation frame; coalescing them avoids unnecessary React
  // re-renders. The hook still ends up rendering ~once per animation frame.
  const pendingFrameRef = useRef(null)
  const rafIdRef = useRef(null)

  const connect = useCallback(() => {
    if (disposedRef.current) return
    const url = `${wsUrl}?session=${encodeURIComponent(sessionId)}&role=frontend`
    const sock = new WebSocket(url, [`${SUBPROTOCOL_PREFIX}${token}`])
    socketRef.current = sock

    sock.binaryType = 'arraybuffer'

    sock.onopen = () => {
      reconnectAttemptRef.current = 0
      setStatus('connected')
    }

    // Schedule a coalesced screenshot update. Multiple frames arriving inside
    // one animation frame collapse to a single React render — keeps the
    // browser responsive at the 5-10 fps Fargate streams.
    const scheduleFrameFlush = () => {
      if (rafIdRef.current === null && typeof requestAnimationFrame === 'function') {
        rafIdRef.current = requestAnimationFrame(() => {
          rafIdRef.current = null
          if (pendingFrameRef.current !== null) {
            setScreenshotUrl(pendingFrameRef.current)
            pendingFrameRef.current = null
          }
        })
      } else if (rafIdRef.current === null) {
        // No rAF available (jsdom in tests, SSR): write through synchronously.
        setScreenshotUrl(pendingFrameRef.current)
        pendingFrameRef.current = null
      }
    }

    sock.onmessage = (ev) => {
      if (typeof ev.data === 'string') {
        const frame = parseTextFrame(ev.data)
        if (!frame) return  // malformed; drop
        if (frame.action === ACTIONS_IN.STATUS) {
          setStatus(frame.status)
          if (frame.status === 'submitted') {
            submittedReceived({ session_id: sessionId })
            onSubmitted?.()
          }
          if (frame.status === 'captcha') {
            captchaDetected({ session_id: sessionId, type: frame.type ?? 'unknown' })
          }
        } else if (frame.action === ACTIONS_IN.FIELDS) {
          setFields(frame.fields || [])
        } else if (frame.action === ACTIONS_IN.FRAME && typeof frame.jpeg === 'string') {
          // JPEG screenshot wrapped in JSON envelope (API Gateway can't deliver
          // binary opcodes — see wsProtocol.js doc-comment + probe Layer #9).
          // The base64 IS the data-URL payload; no decode/re-encode needed.
          pendingFrameRef.current = `data:image/jpeg;base64,${frame.jpeg}`
          scheduleFrameFlush()
        }
        // FIELD_FILLED is informational; consumer can subscribe later if needed
        return
      }
      // Binary frame — defensive fallback. Currently dead path on AWS API
      // Gateway WebSocket (which always coerces to Text). Kept for portability
      // if the transport ever changes.
      if (ev.data instanceof ArrayBuffer) {
        pendingFrameRef.current = binaryFrameToDataUrl(ev.data)
        scheduleFrameFlush()
      }
    }

    sock.onerror = (e) => setError(e?.message || 'WebSocket error')

    sock.onclose = (ev) => {
      if (disposedRef.current || ev.code === 1000) {
        setStatus('closed')
        return
      }
      // Unexpected close — schedule a reconnect with backoff
      const attempt = reconnectAttemptRef.current
      if (attempt >= RECONNECT_DELAYS_MS.length) {
        setStatus('error')
        setError('reconnect_exhausted')
        return
      }
      const delay = RECONNECT_DELAYS_MS[attempt]
      reconnectAttemptRef.current = attempt + 1
      setStatus('reconnecting')
      sessionReconnected({ session_id: sessionId, attempt: attempt + 1 })
      reconnectTimerRef.current = setTimeout(connect, delay)
    }
  }, [wsUrl, sessionId, token, onSubmitted])

  useEffect(() => {
    disposedRef.current = false
    connect()
    return () => {
      disposedRef.current = true
      clearTimeout(reconnectTimerRef.current)
      if (rafIdRef.current !== null && typeof cancelAnimationFrame === 'function') {
        cancelAnimationFrame(rafIdRef.current)
        rafIdRef.current = null
      }
      socketRef.current?.close(1000, 'unmount')
    }
  }, [connect])

  const sendAction = useCallback((payload) => {
    const sock = socketRef.current
    // readyState === 1 is OPEN; use the literal to avoid depending on WebSocket.OPEN
    // being defined (e.g. in test environments where the global is mocked)
    if (!sock || sock.readyState !== 1) return false
    sock.send(JSON.stringify(payload))
    return true
  }, [])

  const dispose = useCallback(() => {
    disposedRef.current = true
    clearTimeout(reconnectTimerRef.current)
    socketRef.current?.close(1000, 'user_close')
  }, [])

  return { status, screenshotUrl, fields, error, sendAction, dispose }
}
