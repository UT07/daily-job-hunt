import { useEffect, useRef, useState } from 'react'
import { useBrowserSession } from '../../hooks/useBrowserSession'
import { SessionStatusBadge } from './SessionStatusBadge'
import { ACTIONS_OUT } from '../../lib/wsProtocol'
import { fillAllSent } from '../../lib/applyTelemetry'

export function BrowserSessionView({
  wsUrl, sessionId, token,
  preview,
  onSubmitted,
}) {
  const { status, screenshotUrl, sendAction, dispose } = useBrowserSession({
    wsUrl, sessionId, token,
  })
  const [manualClickArmed, setManualClickArmed] = useState(false)
  const [paused, setPaused] = useState(false)
  const [typeText, setTypeText] = useState('')
  const submittedFiredRef = useRef(false)
  const imgRef = useRef(null)

  // Fire onSubmitted once when status hits 'submitted'.
  useEffect(() => {
    if (status === 'submitted' && !submittedFiredRef.current) {
      submittedFiredRef.current = true
      onSubmitted?.()
    }
  }, [status, onSubmitted])

  function handleFillAll() {
    const answers = {}
    for (const q of preview?.custom_questions ?? []) {
      if (q.ai_answer != null) answers[q.id] = q.ai_answer
    }
    fillAllSent({ session_id: sessionId, answer_count: Object.keys(answers).length })
    sendAction({ action: ACTIONS_OUT.FILL_ALL, answers })
  }

  function handleSubmit() {
    sendAction({ action: ACTIONS_OUT.SUBMIT })
  }

  function handleEndSession() {
    dispose()
  }

  function handleTypeSend() {
    if (!typeText) return
    sendAction({ action: ACTIONS_OUT.TYPE, text: typeText })
    setTypeText('')
  }

  function handleStreamClick(e) {
    if (!manualClickArmed) return
    const rect = imgRef.current?.getBoundingClientRect()
    if (!rect) return
    // Browser session reports a 1280×800 viewport; map click coords from the
    // rendered img back to that frame.
    const xRatio = (e.clientX - rect.left) / rect.width
    const yRatio = (e.clientY - rect.top) / rect.height
    sendAction({
      action: ACTIONS_OUT.CLICK,
      x: Math.round(xRatio * 1280),
      y: Math.round(yRatio * 800),
      button: 'left',
    })
    setManualClickArmed(false)
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <SessionStatusBadge status={status} />
        {status === 'captcha' && (
          <div className="text-sm font-mono">
            <span className="font-bold">Captcha detected.</span> Solving…
          </div>
        )}
      </div>

      <div
        className={`relative border-2 border-black bg-stone-200 min-h-[400px] ${
          manualClickArmed ? 'cursor-crosshair' : ''
        }`}
      >
        {screenshotUrl ? (
          <img
            ref={imgRef}
            src={screenshotUrl}
            alt="browser stream"
            className="w-full block"
            onClick={handleStreamClick}
          />
        ) : (
          <div className="flex items-center justify-center min-h-[400px] font-mono text-sm text-stone-600">
            Connecting to browser session… (this can take ~30 seconds the first time)
          </div>
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={handleFillAll}
          disabled={status !== 'ready' || paused}
          className="px-3 py-2 border-2 border-black bg-yellow-300 hover:bg-yellow-400 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Fill all
        </button>
        <button
          type="button"
          onClick={handleSubmit}
          disabled={status !== 'ready' && status !== 'filling'}
          className="px-3 py-2 border-2 border-black bg-green-400 hover:bg-green-500 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Submit
        </button>
        <button
          type="button"
          onClick={() => setManualClickArmed((v) => !v)}
          className={`px-3 py-2 border-2 border-black ${
            manualClickArmed ? 'bg-blue-300' : 'bg-white hover:bg-blue-100'
          }`}
        >
          {manualClickArmed ? 'Cancel manual click' : 'Manual click'}
        </button>
        <button
          type="button"
          onClick={() => setPaused((p) => !p)}
          className="px-3 py-2 border-2 border-black bg-white hover:bg-amber-100"
        >
          {paused ? 'Resume' : 'Pause'}
        </button>
        <button
          type="button"
          onClick={handleEndSession}
          className="px-3 py-2 border-2 border-black bg-stone-200 hover:bg-stone-300 ml-auto"
        >
          End session
        </button>
      </div>

      <details className="border-2 border-black p-2">
        <summary className="cursor-pointer font-mono text-sm">Type</summary>
        <div className="flex gap-2 mt-2">
          <input
            type="text"
            placeholder="Text to type into the focused field"
            value={typeText}
            onChange={(e) => setTypeText(e.target.value)}
            className="flex-1 border border-black px-2 py-1 font-mono text-sm"
          />
          <button
            type="button"
            onClick={handleTypeSend}
            className="px-3 py-1 border-2 border-black bg-yellow-300 hover:bg-yellow-400"
          >
            Send type
          </button>
        </div>
      </details>
    </div>
  )
}
