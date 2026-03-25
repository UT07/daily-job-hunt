import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { apiCall } from '../api'

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
    <div className="fixed bottom-0 left-0 right-0 z-50 bg-slate-800 border-t border-slate-700 shadow-lg">
      <div className="max-w-4xl mx-auto px-4 py-4 sm:py-5 flex flex-col sm:flex-row items-start sm:items-center gap-3 sm:gap-4">
        <p className="text-sm text-slate-300 leading-relaxed flex-1">
          We process your data to match jobs and tailor resumes. By continuing, you consent to our
          data processing.{' '}
          <Link
            to="/privacy"
            className="underline text-blue-400 hover:text-blue-300 font-medium transition"
          >
            Learn More
          </Link>
        </p>
        <button
          onClick={handleAccept}
          disabled={accepting}
          className="bg-blue-600 hover:bg-blue-500 text-white px-5 py-2 rounded-lg text-sm font-medium transition
            focus:outline-none focus:ring-2 focus:ring-blue-400 focus:ring-offset-2 focus:ring-offset-slate-800
            disabled:opacity-50 disabled:cursor-not-allowed
            inline-flex items-center gap-2 shrink-0"
        >
          {accepting && <span className="spinner" />}
          Accept
        </button>
      </div>
    </div>
  )
}
