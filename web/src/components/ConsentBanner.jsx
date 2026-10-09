import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { apiCall, apiGet } from '../api'
import { useAuth } from '../auth/useAuth'
import { hasLocalConsent, setLocalConsent } from '../lib/userStorage'
import Button from './ui/Button'

export default function ConsentBanner() {
  const { user } = useAuth()
  const userId = user?.id
  const [visible, setVisible] = useState(false)
  const [accepting, setAccepting] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!userId) return
    // Fast path: THIS user already consented in this browser. Keyed by user
    // id -- the old global key let the next person on a shared browser
    // inherit someone else's consent.
    if (hasLocalConsent(userId)) return

    // Slow path: the server's record (they may have consented on another
    // device, or storage was cleared on sign-out).
    let cancelled = false
    apiGet('/api/profile')
      .then((profile) => {
        if (cancelled) return
        if (profile?.gdpr_consent_at) {
          setLocalConsent(userId)
          setVisible(false)
        } else {
          setVisible(true)
        }
      })
      .catch(() => {
        // Profile fetch failed — show banner as fallback
        if (!cancelled) setVisible(true)
      })
    return () => { cancelled = true }
  }, [userId])

  async function handleAccept() {
    setAccepting(true)
    setError(null)
    try {
      await apiCall('/api/gdpr/consent', { consent: true })
    } catch (e) {
      // Not recorded. Recording it locally anyway (as this used to) hid the
      // banner for good while the server had no consent on file.
      setError(e?.message || 'request failed')
      setAccepting(false)
      return
    }
    setLocalConsent(userId)
    setVisible(false)
    setAccepting(false)
  }

  if (!visible) return null

  return (
    <div className="fixed bottom-0 left-0 right-0 z-50 bg-yellow border-t-2 border-black shadow-brutal">
      <div className="max-w-4xl mx-auto px-4 py-4 sm:py-5 flex flex-col sm:flex-row items-start sm:items-center gap-3 sm:gap-4">
        <p className="text-sm text-black leading-relaxed flex-1 font-bold">
          We process your data to match jobs and tailor resumes. By continuing, you consent to our
          data processing.{' '}
          <Link
            to="/privacy"
            className="underline text-black hover:text-stone-700 transition"
          >
            Learn More
          </Link>
        </p>
        <Button
          variant="primary"
          size="sm"
          onClick={handleAccept}
          disabled={accepting}
        >
          {accepting && <span className="spinner" />}
          Accept
        </Button>
      </div>
      {error && (
        <p role="alert" className="max-w-4xl mx-auto px-4 pb-3 text-sm font-bold text-error">
          Couldn't record your consent ({error}). Please try again.
        </p>
      )}
    </div>
  )
}
