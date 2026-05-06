import { createContext, useCallback, useContext, useState } from 'react'

const AutoApplyContext = createContext(null)

export function AutoApplyProvider({ children }) {
  const [activeSessionId, setActiveSessionId] = useState(null)
  const [activeJobId, setActiveJobId] = useState(null)

  const beginSession = useCallback(({ sessionId, jobId }) => {
    if (activeSessionId && activeSessionId !== sessionId) {
      // Caller should have called endSession first.
      // Throwing here mirrors the backend's 409 session_active_for_different_job.
      throw new Error('session_already_active')
    }
    setActiveSessionId(sessionId)
    setActiveJobId(jobId)
  }, [activeSessionId])

  const endSession = useCallback(() => {
    setActiveSessionId(null)
    setActiveJobId(null)
  }, [])

  const value = { activeSessionId, activeJobId, beginSession, endSession }
  return <AutoApplyContext.Provider value={value}>{children}</AutoApplyContext.Provider>
}

export function useAutoApply() {
  const ctx = useContext(AutoApplyContext)
  if (!ctx) {
    throw new Error('useAutoApply must be called inside <AutoApplyProvider>')
  }
  return ctx
}
