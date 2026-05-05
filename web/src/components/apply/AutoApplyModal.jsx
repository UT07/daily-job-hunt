import { useEffect, useRef, useState } from 'react'
import { apiCall } from '../../api'
import { useApplyPreview } from '../../hooks/useApplyPreview'
import { useFocusTrap } from '../../hooks/useFocusTrap'
import { QuestionsTable } from './QuestionsTable'
import { ProfileSnapshot } from './ProfileSnapshot'
import { EmptyPreviewState } from './EmptyPreviewState'
import { modalOpened, modalDismissed, fieldCopied, atsOpened, markedApplied } from '../../lib/applyTelemetry'

export function AutoApplyModal({ job, isOpen, onClose, onMarkApplied }) {
  const jobId = job.id || job.job_id
  const { data: preview, isLoading, refetch } = useApplyPreview(jobId, { enabled: isOpen })
  const [atsOpenedState, setAtsOpenedState] = useState(false)
  const [submitError, setSubmitError] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const openedFiredRef = useRef(false)
  const wasOpenRef = useRef(isOpen)
  const dialogRef = useFocusTrap(isOpen)

  // Modal-opened telemetry (fire once per open)
  useEffect(() => {
    if (isOpen && !openedFiredRef.current) {
      modalOpened({ job_id: jobId, platform: job.apply_platform, reason: 'eligible' })
      openedFiredRef.current = true
    }
  }, [isOpen, jobId, job.apply_platform])

  // Modal-dismissed telemetry (fire when modal transitions from open → closed without mark-applied)
  useEffect(() => {
    if (wasOpenRef.current && !isOpen) {
      modalDismissed({ job_id: jobId, platform: job.apply_platform, ats_was_opened: atsOpenedState })
      openedFiredRef.current = false
      setAtsOpenedState(false)
    }
    wasOpenRef.current = isOpen
  }, [isOpen, jobId, job.apply_platform, atsOpenedState])

  // ESC key closes the modal. Tab-cycle focus trap + initial-focus / restore
  // on close are handled by useFocusTrap (see hooks/useFocusTrap.js).
  useEffect(() => {
    if (!isOpen) return
    const handleKey = (e) => { if (e.key === 'Escape') onClose?.() }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [isOpen, onClose])

  if (!isOpen) return null

  const handleOpenAts = () => {
    window.open(job.apply_url, '_blank')
    atsOpened({ job_id: jobId, platform: job.apply_platform })
    setAtsOpenedState(true)
  }

  const handleMarkApplied = async () => {
    setSubmitError(null)
    setSubmitting(true)
    try {
      await apiCall('/api/apply/record', {
        job_id: jobId,
        platform: job.apply_platform,
        accepted_at: new Date().toISOString(),
      })
      markedApplied({ job_id: jobId, platform: job.apply_platform, ats_was_opened: atsOpenedState })
      onMarkApplied?.()
      onClose?.()
    } catch (e) {
      setSubmitError(e.message || 'Mark-applied failed')
    } finally {
      setSubmitting(false)
    }
  }

  const questions = preview?.custom_questions ?? []

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center" onClick={onClose}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="apply-modal-title"
        tabIndex={-1}
        className="bg-cream border-4 border-black p-6 max-w-3xl w-full max-h-[90vh] overflow-auto outline-none"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="apply-modal-title" className="text-xl font-bold mb-2 font-mono">
          Smart Apply: {job.company} — {job.title}
        </h2>

        {isLoading && <p className="text-sm">Loading preview…</p>}

        {!isLoading && preview && (
          <>
            <div className="flex gap-2 mb-4">
              {preview.resume?.s3_url && (
                <a href={preview.resume.s3_url} target="_blank" rel="noopener" className="px-3 py-1 border border-black bg-white">
                  📄 Tailored Resume ({preview.resume.filename})
                </a>
              )}
            </div>

            {/* Cover letter is INLINE TEXT (not a URL) — copy-paste flow */}
            {preview.cover_letter?.text && (
              <div className="mb-4 border-2 border-black p-3">
                <div className="flex justify-between items-center mb-2">
                  <span className="font-bold font-mono">Cover letter</span>
                  <button
                    type="button"
                    onClick={async () => {
                      try {
                        await navigator.clipboard.writeText(preview.cover_letter.text)
                        fieldCopied({ job_id: jobId, field_name: '__cover_letter__' })
                      } catch (e) {
                        // Clipboard API can reject in iframes / lost focus / denied permissions.
                        // Fire fieldCopied with an error flag so telemetry distinguishes
                        // a real copy from a silent failure.
                        fieldCopied({ job_id: jobId, field_name: '__cover_letter__', error: e?.message || 'Clipboard unavailable' })
                      }
                    }}
                    className="px-2 py-1 border border-black hover:bg-yellow-200"
                  >
                    📋 Copy
                  </button>
                </div>
                <pre className="text-sm whitespace-pre-wrap font-mono">{preview.cover_letter.text}</pre>
              </div>
            )}

            {questions.length === 0 ? (
              <EmptyPreviewState onRetry={refetch} />
            ) : (
              <QuestionsTable
                questions={questions}
                onCopy={({ field_name }) => fieldCopied({ job_id: jobId, field_name })}
              />
            )}

            <div className="my-4">
              <ProfileSnapshot snapshot={preview.profile} />
            </div>
          </>
        )}

        {submitError && <p className="text-red-700 text-sm mb-2">Couldn't mark applied: {submitError}</p>}

        <div className="flex justify-end gap-2 mt-4">
          <button type="button" onClick={onClose} className="px-4 py-2 border-2 border-black bg-white">Cancel</button>
          {atsOpenedState ? (
            <button
              type="button"
              onClick={handleMarkApplied}
              disabled={submitting}
              className="px-4 py-2 border-2 border-black bg-yellow-300 hover:bg-yellow-400 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {submitting ? 'Recording…' : 'I submitted — mark applied'}
            </button>
          ) : (
            <button type="button" onClick={handleOpenAts} className="px-4 py-2 border-2 border-black bg-yellow-300 hover:bg-yellow-400">
              Open ATS in new tab
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
