import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { apiCall } from '../api'
import Button from './ui/Button'

const CONSENT_KEY = 'gdpr_consent'

export default function ConsentBanner() {
  const [visible, setVisible] = useState(false)
  const [accepting, setAccepting] = useState(false)

  useEffect(() => {
    const consent = localStorage.getItem(CONSENT_KEY)
    if (consent !== 'true') {
      setVisible(true)
    }
  }, [])

  async function handleAccept() {
    setAccepting(true)
    try {
      await apiCall('/api/gdpr/consent', { consent: true })
    } catch (e) {
      // Record consent locally even if API is unavailable
      console.warn('Consent API:', e.message)
    }
    localStorage.setItem(CONSENT_KEY, 'true')
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
    </div>
  )
}
