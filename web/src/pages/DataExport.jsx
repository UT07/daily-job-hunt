import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/useAuth'
import { apiGetBlob, apiDelete } from '../api'
import LoginPage from './LoginPage'

export default function DataExport() {
  const { user, loading: authLoading, signOut } = useAuth()
  const navigate = useNavigate()

  const [exporting, setExporting] = useState(false)
  const [exportStatus, setExportStatus] = useState(null)

  const [deleting, setDeleting] = useState(false)
  const [deleteStatus, setDeleteStatus] = useState(null)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [deleteConfirmText, setDeleteConfirmText] = useState('')

  const consentGiven = typeof window !== 'undefined' && localStorage.getItem('gdpr_consent') === 'true'

  async function handleExport() {
    setExporting(true)
    setExportStatus(null)
    try {
      const blob = await apiGetBlob('/api/gdpr/export')
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `job-hunt-data-export-${new Date().toISOString().split('T')[0]}.zip`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      setExportStatus({ type: 'success', message: 'Your data has been downloaded successfully.' })
    } catch (e) {
      setExportStatus({ type: 'error', message: `Export failed: ${e.message}` })
    } finally {
      setExporting(false)
    }
  }

  async function handleDelete() {
    if (deleteConfirmText !== 'DELETE') return
    setDeleting(true)
    setDeleteStatus(null)
    try {
      await apiDelete('/api/gdpr/delete')
      localStorage.removeItem('gdpr_consent')
      setDeleteStatus({ type: 'success', message: 'Your account and all data have been permanently deleted.' })
      // Sign out and redirect after a short delay
      setTimeout(async () => {
        await signOut()
        navigate('/')
      }, 2000)
    } catch (e) {
      setDeleteStatus({ type: 'error', message: `Deletion failed: ${e.message}` })
    } finally {
      setDeleting(false)
    }
  }

  if (authLoading) {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center">
        <div className="text-slate-400 text-sm">Loading...</div>
      </div>
    )
  }

  if (!user) {
    return <LoginPage />
  }

  return (
    <div className="min-h-screen bg-slate-900">
      {/* Header */}
      <header className="bg-slate-800 border-b border-slate-700 sticky top-0 z-10">
        <div className="max-w-3xl mx-auto px-4 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-2xl">🎯</span>
            <h1 className="text-xl font-bold text-white">Data & Privacy</h1>
          </div>
          <div className="flex items-center gap-4">
            <Link
              to="/"
              className="text-sm text-slate-400 hover:text-white font-medium transition"
            >
              Tailor
            </Link>
            <Link
              to="/dashboard"
              className="text-sm text-slate-400 hover:text-white font-medium transition"
            >
              Dashboard
            </Link>
            <span className="text-sm text-slate-500 hidden sm:block">{user.email}</span>
            <button
              onClick={signOut}
              className="text-sm text-slate-400 hover:text-white font-medium transition"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-4 py-8 space-y-6">
        {/* Consent Status */}
        <div className="bg-slate-800 rounded-xl shadow-lg border border-slate-700 p-6">
          <h2 className="text-base font-semibold text-white mb-3">Consent Status</h2>
          <div className="flex items-center gap-3">
            <div
              className={`w-3 h-3 rounded-full ${consentGiven ? 'bg-emerald-500' : 'bg-amber-500'}`}
            />
            <span className="text-sm text-slate-300">
              {consentGiven
                ? 'You have consented to data processing.'
                : 'You have not yet provided consent for data processing.'}
            </span>
          </div>
          <p className="text-xs text-slate-500 mt-2">
            View our{' '}
            <Link to="/privacy" className="text-blue-400 hover:text-blue-300 underline">
              Privacy Policy
            </Link>{' '}
            for details on how your data is used.
          </p>
        </div>

        {/* Export Data */}
        <div className="bg-slate-800 rounded-xl shadow-lg border border-slate-700 p-6">
          <h2 className="text-base font-semibold text-white mb-1">Export My Data</h2>
          <p className="text-sm text-slate-400 mb-4">
            Download a ZIP archive containing all your personal data, including your profile,
            job search history, match scores, resumes, and preferences. This fulfills your
            GDPR Article 20 right to data portability.
          </p>
          <button
            onClick={handleExport}
            disabled={exporting}
            className="bg-blue-600 hover:bg-blue-500 text-white px-5 py-2.5 rounded-lg text-sm font-medium transition
              focus:outline-none focus:ring-2 focus:ring-blue-400 focus:ring-offset-2 focus:ring-offset-slate-900
              disabled:opacity-50 disabled:cursor-not-allowed
              inline-flex items-center gap-2"
          >
            {exporting && <span className="spinner" />}
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            {exporting ? 'Preparing export...' : 'Export My Data'}
          </button>
          {exportStatus && (
            <div
              className={`mt-3 p-3 rounded-lg text-sm border
                ${exportStatus.type === 'success' ? 'bg-emerald-900/30 border-emerald-800 text-emerald-300' : ''}
                ${exportStatus.type === 'error' ? 'bg-red-900/30 border-red-800 text-red-300' : ''}`}
            >
              {exportStatus.message}
            </div>
          )}
        </div>

        {/* Delete Account */}
        <div className="bg-slate-800 rounded-xl shadow-lg border border-red-800/50 p-6">
          <h2 className="text-base font-semibold text-red-400 mb-1">Delete My Account</h2>
          <p className="text-sm text-slate-400 mb-4">
            Permanently delete your account and all associated data. This action is
            irreversible and fulfills your GDPR Article 17 right to erasure. All your
            profile data, job history, resumes, and scores will be permanently removed.
          </p>

          {!showDeleteConfirm ? (
            <button
              onClick={() => setShowDeleteConfirm(true)}
              className="bg-red-600 hover:bg-red-500 text-white px-5 py-2.5 rounded-lg text-sm font-medium transition
                focus:outline-none focus:ring-2 focus:ring-red-400 focus:ring-offset-2 focus:ring-offset-slate-900
                inline-flex items-center gap-2"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
              Delete My Account
            </button>
          ) : (
            <div className="border border-red-800/50 bg-red-900/20 rounded-lg p-4">
              <p className="text-sm text-red-300 font-medium mb-3">
                This will permanently delete all your data. Type <strong className="text-white">DELETE</strong> to confirm.
              </p>
              <div className="flex items-center gap-3">
                <input
                  type="text"
                  value={deleteConfirmText}
                  onChange={(e) => setDeleteConfirmText(e.target.value)}
                  placeholder="Type DELETE to confirm"
                  className="flex-1 bg-slate-700/50 border border-red-800/50 rounded-lg px-3 py-2 text-sm text-white focus:ring-2 focus:ring-red-500 focus:border-red-500 placeholder:text-slate-500"
                />
                <button
                  onClick={handleDelete}
                  disabled={deleteConfirmText !== 'DELETE' || deleting}
                  className="bg-red-600 hover:bg-red-500 text-white px-5 py-2 rounded-lg text-sm font-medium transition
                    focus:outline-none focus:ring-2 focus:ring-red-400 focus:ring-offset-2 focus:ring-offset-slate-900
                    disabled:opacity-50 disabled:cursor-not-allowed
                    inline-flex items-center gap-2"
                >
                  {deleting && <span className="spinner" />}
                  {deleting ? 'Deleting...' : 'Confirm Delete'}
                </button>
                <button
                  onClick={() => { setShowDeleteConfirm(false); setDeleteConfirmText('') }}
                  className="text-sm text-slate-400 hover:text-white font-medium transition px-3 py-2"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {deleteStatus && (
            <div
              className={`mt-3 p-3 rounded-lg text-sm border
                ${deleteStatus.type === 'success' ? 'bg-emerald-900/30 border-emerald-800 text-emerald-300' : ''}
                ${deleteStatus.type === 'error' ? 'bg-red-900/30 border-red-800 text-red-300' : ''}`}
            >
              {deleteStatus.message}
            </div>
          )}
        </div>
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-700 mt-12">
        <div className="max-w-3xl mx-auto px-4 py-4 text-center text-xs text-slate-500">
          Built by Utkarsh Singh -- FastAPI + React + Tailwind
        </div>
      </footer>
    </div>
  )
}
