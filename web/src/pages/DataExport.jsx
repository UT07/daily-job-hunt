import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/useAuth'
import { apiGetBlob, apiDelete } from '../api'
import Card, { CardHeader, CardBody } from '../components/ui/Card'
import Button from '../components/ui/Button'
import Input from '../components/ui/Input'
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
      <div className="flex items-center justify-center py-20">
        <div className="text-stone-400 text-sm font-mono">Loading...</div>
      </div>
    )
  }

  if (!user) {
    return <LoginPage />
  }

  return (
    <div>
      <h1 className="text-2xl font-heading font-bold text-black tracking-tight mb-6">Data &amp; Privacy</h1>

      <div className="space-y-6">
        {/* Consent Status */}
        <Card>
          <CardHeader>
            <h2 className="text-base font-heading font-bold text-black">Consent Status</h2>
          </CardHeader>
          <CardBody>
            <div className="flex items-center gap-3">
              <div
                className={`w-3 h-3 border-2 border-black ${consentGiven ? 'bg-success' : 'bg-yellow'}`}
              />
              <span className="text-sm text-stone-700 font-bold">
                {consentGiven
                  ? 'You have consented to data processing.'
                  : 'You have not yet provided consent for data processing.'}
              </span>
            </div>
            <p className="text-xs text-stone-400 mt-2 font-mono">
              View our{' '}
              <Link to="/privacy" className="text-info hover:underline font-bold">
                Privacy Policy
              </Link>{' '}
              for details on how your data is used.
            </p>
          </CardBody>
        </Card>

        {/* Export Data */}
        <Card>
          <CardHeader>
            <div>
              <h2 className="text-base font-heading font-bold text-black">Export My Data</h2>
              <p className="text-sm text-stone-500 mt-0.5">
                Download a ZIP archive containing all your personal data, including your profile,
                job search history, match scores, resumes, and preferences. This fulfills your
                GDPR Article 20 right to data portability.
              </p>
            </div>
          </CardHeader>
          <CardBody>
            <Button variant="accent" onClick={handleExport} disabled={exporting}>
              {exporting && <span className="spinner" />}
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              {exporting ? 'Preparing export...' : 'Export My Data'}
            </Button>
            {exportStatus && (
              <div
                className={`mt-3 p-3 text-sm border-2
                  ${exportStatus.type === 'success' ? 'bg-success-light border-success text-success' : ''}
                  ${exportStatus.type === 'error' ? 'bg-error-light border-error text-error' : ''}`}
              >
                {exportStatus.message}
              </div>
            )}
          </CardBody>
        </Card>

        {/* Delete Account */}
        <Card className="border-error">
          <CardHeader className="border-b-2 border-error">
            <div>
              <h2 className="text-base font-heading font-bold text-error">Delete My Account</h2>
              <p className="text-sm text-stone-500 mt-0.5">
                Permanently delete your account and all associated data. This action is
                irreversible and fulfills your GDPR Article 17 right to erasure. All your
                profile data, job history, resumes, and scores will be permanently removed.
              </p>
            </div>
          </CardHeader>
          <CardBody>
            {!showDeleteConfirm ? (
              <Button variant="danger" onClick={() => setShowDeleteConfirm(true)}>
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                </svg>
                Delete My Account
              </Button>
            ) : (
              <div className="border-2 border-error bg-error-light p-4">
                <p className="text-sm text-error font-bold mb-3">
                  This will permanently delete all your data. Type <strong className="text-black">DELETE</strong> to confirm.
                </p>
                <div className="flex items-center gap-3">
                  <Input
                    type="text"
                    value={deleteConfirmText}
                    onChange={(e) => setDeleteConfirmText(e.target.value)}
                    placeholder="Type DELETE to confirm"
                  />
                  <Button
                    variant="danger"
                    onClick={handleDelete}
                    disabled={deleteConfirmText !== 'DELETE' || deleting}
                  >
                    {deleting && <span className="spinner" />}
                    {deleting ? 'Deleting...' : 'Confirm Delete'}
                  </Button>
                  <Button
                    variant="ghost"
                    onClick={() => { setShowDeleteConfirm(false); setDeleteConfirmText('') }}
                  >
                    Cancel
                  </Button>
                </div>
              </div>
            )}

            {deleteStatus && (
              <div
                className={`mt-3 p-3 text-sm border-2
                  ${deleteStatus.type === 'success' ? 'bg-success-light border-success text-success' : ''}
                  ${deleteStatus.type === 'error' ? 'bg-error-light border-error text-error' : ''}`}
              >
                {deleteStatus.message}
              </div>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  )
}
