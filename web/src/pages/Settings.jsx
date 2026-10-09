import { useState, useEffect, useRef, useCallback } from 'react'
import { useAuth } from '../auth/useAuth'
import { useUserProfile } from '../hooks/useUserProfile'
import { apiGet, apiPut, apiUpload, apiDelete } from '../api'
import Card, { CardHeader, CardBody } from '../components/ui/Card'
import Input from '../components/ui/Input'
import { NoticePeriodPicker } from '../components/ui/NoticePeriodPicker'
import Button from '../components/ui/Button'
import LoginPage from './LoginPage'
import { isAcceptedResumeFile, resumeRejectionMessage } from '../lib/resumeUploadFile'

function TagInput({ value, onChange, placeholder }) {
  const [input, setInput] = useState('')

  function addTag() {
    const tag = input.trim()
    if (tag && !value.includes(tag)) {
      onChange([...value, tag])
    }
    setInput('')
  }

  function removeTag(tag) {
    onChange(value.filter((t) => t !== tag))
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter') {
      e.preventDefault()
      addTag()
    }
    if (e.key === 'Backspace' && !input && value.length) {
      removeTag(value[value.length - 1])
    }
  }

  return (
    <div className="w-full border-2 border-black px-3 py-2 flex flex-wrap gap-2 bg-white focus-within:shadow-brutal transition-shadow">
      {value.map((tag) => (
        <span
          key={tag}
          className="inline-flex items-center gap-1 bg-yellow-light border-2 border-yellow-dark text-black text-sm px-2.5 py-0.5 font-mono"
        >
          {tag}
          <button
            type="button"
            onClick={() => removeTag(tag)}
            className="text-stone-500 hover:text-black"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </span>
      ))}
      <input
        type="text"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={handleKeyDown}
        onBlur={addTag}
        placeholder={value.length === 0 ? placeholder : ''}
        className="flex-1 min-w-[120px] outline-none text-sm bg-transparent text-black placeholder:text-stone-400 font-mono"
      />
    </div>
  )
}

function StatusMessage({ status }) {
  if (!status) return null
  const styles = {
    success: 'bg-success-light border-2 border-success text-success',
    error: 'bg-error-light border-2 border-error text-error',
    info: 'bg-info-light border-2 border-info text-info',
  }
  return (
    <div className={`mt-3 p-3 text-sm ${styles[status.type] || styles.info}`}>
      {status.message}
    </div>
  )
}

// A section whose GET failed renders blanks or component defaults, not the
// user's data. Saving that would overwrite the real row with them (PUT
// /api/profile drops only None, so '' is written), so the section says so and
// its Save stays disabled until a reload succeeds.
function LoadFailed({ what, error, onRetry }) {
  return (
    <div role="alert" className="mb-4 p-3 text-sm bg-error-light border-2 border-error text-error flex items-center justify-between gap-3">
      <span>
        Couldn't load your {what}{error ? ` (${error})` : ''}. Saving is disabled so the
        blank form can't overwrite what is stored.
      </span>
      <Button size="sm" variant="secondary" onClick={onRetry} aria-label={`Retry loading ${what}`}>
        Retry
      </Button>
    </div>
  )
}

// 'loading' | 'loaded' | { error: string }
const isLoaded = (state) => state === 'loaded'
const loadError = (state) => (state && typeof state === 'object' ? state.error : null)

function ProfileSection({ profile, setProfile, loadState = 'loaded', onRetry }) {
  const { refresh: refreshProfileContext } = useUserProfile()
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState(null)

  function updateField(field, value) {
    setProfile((prev) => ({ ...prev, [field]: value }))
  }

  function updateWorkAuth(index, field, value) {
    setProfile((prev) => {
      const auths = [...prev.work_authorizations]
      auths[index] = { ...auths[index], [field]: value }
      return { ...prev, work_authorizations: auths }
    })
  }

  function addWorkAuth() {
    setProfile((prev) => ({
      ...prev,
      work_authorizations: [...prev.work_authorizations, { country: '', status: '' }],
    }))
  }

  function removeWorkAuth(index) {
    setProfile((prev) => ({
      ...prev,
      work_authorizations: prev.work_authorizations.filter((_, i) => i !== index),
    }))
  }

  async function handleSave() {
    if (!isLoaded(loadState)) return
    setSaving(true)
    setStatus(null)
    try {
      const workAuthObj = {}
      for (const auth of profile.work_authorizations) {
        if (auth.country.trim()) {
          workAuthObj[auth.country.trim()] = auth.status.trim()
        }
      }
      // Strip read-only fields that ProfileUpdateRequest rejects via
      // model_config = ConfigDict(extra="forbid"). Same fix the onboarding
      // wizard applied in 70a91a5; Settings had the same bug.
      // eslint-disable-next-line no-unused-vars
      const { email, id, profile_complete, missing_required_fields, onboarding_completed_at, ...payload } = profile
      await apiPut('/api/profile', {
        ...payload,
        work_authorizations: workAuthObj,
      })
      setStatus({ type: 'success', message: 'Profile saved.' })
      // The shell (FinishSetupBanner, the onboarding gate) reads
      // ProfileContext, not this form. Without this the banner kept saying
      // the profile was incomplete until a reload.
      await refreshProfileContext()
    } catch (e) {
      setStatus({ type: 'error', message: `Save failed: ${e.message}` })
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <div>
          <h3 className="text-base font-heading font-bold text-black">Profile</h3>
          <p className="text-sm text-stone-500 mt-0.5">Your personal and contact information.</p>
        </div>
      </CardHeader>
      <CardBody>
        {loadError(loadState) && (
          <LoadFailed what="profile" error={loadError(loadState)} onRetry={onRetry} />
        )}
        <div className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-bold text-black mb-1">Full Name</label>
              <Input
                type="text"
                value={profile.name}
                onChange={(e) => updateField('name', e.target.value)}
                placeholder="Utkarsh Singh"
              />
            </div>
            <div>
              <label className="block text-sm font-bold text-black mb-1">Email</label>
              <Input
                type="email"
                value={profile.email}
                disabled
                className="opacity-60 cursor-not-allowed"
              />
            </div>
            <div>
              <label className="block text-sm font-bold text-black mb-1">Phone</label>
              <Input
                type="tel"
                value={profile.phone}
                onChange={(e) => updateField('phone', e.target.value)}
                placeholder="+353 85 123 4567"
              />
            </div>
            <div>
              <label className="block text-sm font-bold text-black mb-1">Location</label>
              <Input
                type="text"
                value={profile.location}
                onChange={(e) => updateField('location', e.target.value)}
                placeholder="Dublin, Ireland"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-bold text-black mb-1">GitHub URL</label>
              <Input
                type="url"
                value={profile.github_url}
                onChange={(e) => updateField('github_url', e.target.value)}
                placeholder="https://github.com/username"
              />
            </div>
            <div>
              <label className="block text-sm font-bold text-black mb-1">LinkedIn URL</label>
              <Input
                type="url"
                value={profile.linkedin_url}
                onChange={(e) => updateField('linkedin_url', e.target.value)}
                placeholder="https://linkedin.com/in/username"
              />
            </div>
            <div>
              <label className="block text-sm font-bold text-black mb-1">Website</label>
              <Input
                type="url"
                value={profile.website}
                onChange={(e) => updateField('website', e.target.value)}
                placeholder="https://yoursite.com"
              />
            </div>
          </div>

          <div>
            <label className="block text-sm font-bold text-black mb-1">Visa Status</label>
            <Input
              type="text"
              value={profile.visa_status}
              onChange={(e) => updateField('visa_status', e.target.value)}
              placeholder="e.g. Stamp 1G, EU Citizen, H-1B"
            />
          </div>

          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="block text-sm font-bold text-black">Work Authorizations</label>
              <button
                type="button"
                onClick={addWorkAuth}
                className="text-sm text-info hover:underline font-bold"
              >
                + Add country
              </button>
            </div>
            <div className="space-y-2">
              {profile.work_authorizations.map((auth, i) => (
                <div key={i} className="flex gap-2 items-center">
                  <Input
                    type="text"
                    value={auth.country}
                    onChange={(e) => updateWorkAuth(i, 'country', e.target.value)}
                    placeholder="Country (e.g. Ireland)"
                  />
                  <Input
                    type="text"
                    value={auth.status}
                    onChange={(e) => updateWorkAuth(i, 'status', e.target.value)}
                    placeholder="Status (e.g. Stamp 1G)"
                  />
                  <button
                    type="button"
                    onClick={() => removeWorkAuth(i)}
                    className="text-stone-400 hover:text-error p-1 transition shrink-0"
                  >
                    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                    </svg>
                  </button>
                </div>
              ))}
              {profile.work_authorizations.length === 0 && (
                <p className="text-sm text-stone-400 italic">No work authorizations added yet.</p>
              )}
            </div>
          </div>

          <Input label="Salary Expectations" value={profile.salary_expectation_notes || ''}
            onChange={e => updateField('salary_expectation_notes', e.target.value)}
            placeholder="e.g. €70-90k base + equity" />

          <NoticePeriodPicker
            value={profile.notice_period_text || ''}
            onChange={(v) => updateField('notice_period_text', v)}
          />
        </div>

        <div className="mt-5 flex items-center gap-3">
          <Button onClick={handleSave} disabled={saving || !isLoaded(loadState)}>
            {saving && <span className="spinner" />}
            Save Changes
          </Button>
          <StatusMessage status={status} />
        </div>
      </CardBody>
    </Card>
  )
}

function PasswordSection() {
  const { updatePassword } = useAuth()
  const [newPass, setNewPass] = useState('')
  const [confirm, setConfirm] = useState('')
  const [status, setStatus] = useState({ type: '', message: '' })
  const [saving, setSaving] = useState(false)

  async function handleChange() {
    if (newPass.length < 8) {
      setStatus({ type: 'error', message: 'Password must be at least 8 characters' })
      return
    }
    if (newPass !== confirm) {
      setStatus({ type: 'error', message: 'Passwords do not match' })
      return
    }
    setSaving(true)
    setStatus({ type: '', message: '' })
    try {
      await updatePassword(newPass)
      setStatus({ type: 'success', message: 'Password updated successfully' })
      setNewPass(''); setConfirm('')
    } catch (err) {
      setStatus({ type: 'error', message: err.message || 'Failed to update password' })
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <h2 className="text-lg font-heading font-bold">Change Password</h2>
      </CardHeader>
      <CardBody>
        <div className="space-y-4 max-w-md">
          <Input label="New Password" type="password" value={newPass}
            onChange={e => setNewPass(e.target.value)} placeholder="Minimum 8 characters" />
          <Input label="Confirm New Password" type="password" value={confirm}
            onChange={e => setConfirm(e.target.value)} placeholder="Re-enter new password" />
          <StatusMessage status={status} />
          <Button onClick={handleChange} loading={saving} disabled={!newPass || !confirm}>
            Update Password
          </Button>
        </div>
      </CardBody>
    </Card>
  )
}

export function ResumeSection() {
  const fileInputRef = useRef(null)
  const [resumeFile, setResumeFile] = useState(null)
  const [uploadStatus, setUploadStatus] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [resumes, setResumes] = useState([])

  useEffect(() => {
    apiGet('/api/resumes')
      .then((data) => setResumes(Array.isArray(data) ? data : data.resumes || []))
      .catch((e) => console.warn('Failed to load resumes:', e))
  }, [])

  // Deleting is two clicks: the row being deleted may be the résumé every
  // tailoring starts from. And a failure is SHOWN -- it used to go only to
  // console.warn, so a failed delete looked like a click that did nothing.
  const [confirmingId, setConfirmingId] = useState(null)
  const [deletingId, setDeletingId] = useState(null)
  const [deleteError, setDeleteError] = useState(null)

  const resumeName = (resume) =>
    resume.label || resume.filename || resume.name || `Resume ${resume.id}`

  async function handleDelete(resume) {
    setDeletingId(resume.id)
    setDeleteError(null)
    try {
      await apiDelete(`/api/resumes/${resume.id}`)
      setResumes((prev) => prev.filter((r) => r.id !== resume.id))
      setConfirmingId(null)
    } catch (e) {
      console.warn('Failed to delete resume:', e)
      setDeleteError(`Could not delete ${resumeName(resume)}: ${e?.message || 'request failed'}`)
      setConfirmingId(null)
    } finally {
      setDeletingId(null)
    }
  }

  function handleFile(file) {
    // Gate on the extension. This used to require MIME application/pdf while
    // the input advertised .tex/.latex/.pdf, and browsers report a .tex as
    // text/x-tex, application/x-tex, text/plain or "" — so selecting the one
    // format the backend stores VERBATIM did nothing at all.
    if (isAcceptedResumeFile(file)) {
      setResumeFile(file)
      setUploadStatus(null)
    } else if (file) {
      setUploadStatus({ type: 'error', message: resumeRejectionMessage(file) })
    }
  }

  function handleDrop(e) {
    e.preventDefault()
    setDragOver(false)
    handleFile(e.dataTransfer.files[0])
  }

  async function handleUpload() {
    if (!resumeFile) return
    setUploading(true)
    setUploadStatus(null)
    try {
      const result = await apiUpload('/api/resumes/upload', resumeFile)
      // The API tells us when an upload cannot be tailored from —
      // tailorable=false plus a tailoring_warning explaining why. Both fields
      // were added "so the UI can show it, rather than letting the user find
      // out from 'Regenerate failed: Pipeline failed'", and then nothing read
      // them: this reported plain success unconditionally. A PDF uploaded on
      // 2026-09-28 was stored as plain text, the pipeline silently fell back to
      // a 2026-04-05 template, and every resume for a day was built from the
      // wrong base while this box said "successfully".
      //
      // `=== false` rather than `!result.tailorable`, so an older API response
      // without the field is still treated as success.
      if (result && result.tailorable === false) {
        setUploadStatus({
          type: 'info',
          message: result.tailoring_warning
            || 'Saved, but this file cannot be tailored from — tailoring will keep using your most recent LaTeX resume.',
        })
      } else {
        setUploadStatus({ type: 'success', message: 'Resume uploaded and parsed successfully.' })
      }
      setResumeFile(null)
      const data = await apiGet('/api/resumes').catch(() => null)
      if (data) setResumes(Array.isArray(data) ? data : data.resumes || [])
    } catch (e) {
      setUploadStatus({ type: 'error', message: `Upload failed: ${e.message}` })
    } finally {
      setUploading(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <div>
          <h3 className="text-base font-heading font-bold text-black">Resumes</h3>
          <p className="text-sm text-stone-500 mt-0.5">Upload and manage your resume files.</p>
        </div>
      </CardHeader>
      <CardBody>
        {deleteError && (
          <div role="alert" className="mb-3">
            <StatusMessage status={{ type: 'error', message: deleteError }} />
          </div>
        )}
        {resumes.length > 0 ? (
          <ul className="mb-4 divide-y divide-stone-200 border-2 border-black overflow-hidden">
            {resumes.map((resume) => (
              <li key={resume.id} className="flex items-center justify-between px-4 py-3 bg-white hover:bg-yellow-light transition-colors">
                <div>
                  <p className="text-sm font-bold text-black">{resumeName(resume)}</p>
                  {resume.uploaded_at && (
                    <p className="text-xs text-stone-400 mt-0.5 font-mono">
                      {new Date(resume.uploaded_at).toLocaleDateString()}
                    </p>
                  )}
                </div>
                {confirmingId === resume.id ? (
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-bold text-error">Delete {resumeName(resume)}?</span>
                    <Button
                      size="sm"
                      variant="danger"
                      loading={deletingId === resume.id}
                      disabled={deletingId === resume.id}
                      onClick={() => handleDelete(resume)}
                    >
                      Delete
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={deletingId === resume.id}
                      onClick={() => setConfirmingId(null)}
                    >
                      Cancel
                    </Button>
                  </div>
                ) : (
                <button
                  type="button"
                  onClick={() => { setDeleteError(null); setConfirmingId(resume.id) }}
                  className="text-stone-400 hover:text-error p-1.5 transition"
                  title="Delete resume"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                </button>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <div className="text-sm text-stone-500 mb-4 p-3 bg-stone-100 border-2 border-stone-300">
            No resumes uploaded yet.
          </div>
        )}

        <div
          onDrop={handleDrop}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
          onDragLeave={() => setDragOver(false)}
          onClick={() => fileInputRef.current?.click()}
          className={`border-2 border-dashed p-8 text-center cursor-pointer transition
            ${dragOver ? 'border-black bg-yellow-light' : 'border-stone-400 hover:border-black hover:bg-stone-50'}`}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept=".tex,.latex,.pdf"
            className="hidden"
            onChange={(e) => handleFile(e.target.files[0])}
          />
          <svg className="w-8 h-8 mx-auto text-stone-400 mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 16.5V9.75m0 0l3 3m-3-3l-3 3M6.75 19.5a4.5 4.5 0 01-1.41-8.775 5.25 5.25 0 0110.233-2.33 3 3 0 013.758 3.848A3.752 3.752 0 0118 19.5H6.75z" />
          </svg>
          {resumeFile ? (
            <p className="text-sm font-bold text-black">{resumeFile.name} ({(resumeFile.size / 1024).toFixed(1)} KB)</p>
          ) : (
            <p className="text-sm text-stone-500">Drop a PDF here, or click to browse</p>
          )}
        </div>

        {resumeFile && (
          <div className="mt-4">
            <Button onClick={handleUpload} disabled={uploading}>
              {uploading && <span className="spinner" />}
              Upload &amp; Parse
            </Button>
          </div>
        )}

        <StatusMessage status={uploadStatus} />
      </CardBody>
    </Card>
  )
}

function PreferencesSection({ prefs, setPrefs, loadState = 'loaded', onRetry }) {
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState(null)

  function updateField(field, value) {
    setPrefs((prev) => ({ ...prev, [field]: value }))
  }

  function toggleLevel(level) {
    setPrefs((prev) => {
      const levels = prev.experience_levels.includes(level)
        ? prev.experience_levels.filter((l) => l !== level)
        : [...prev.experience_levels, level]
      return { ...prev, experience_levels: levels }
    })
  }

  async function handleSave() {
    if (!isLoaded(loadState)) return
    setSaving(true)
    setStatus(null)
    try {
      await apiPut('/api/search-config', prefs)
      setStatus({ type: 'success', message: 'Search preferences saved.' })
    } catch (e) {
      setStatus({ type: 'error', message: `Save failed: ${e.message}` })
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <div>
          <h3 className="text-base font-heading font-bold text-black">Search Preferences</h3>
          <p className="text-sm text-stone-500 mt-0.5">Configure your automated job search.</p>
        </div>
      </CardHeader>
      <CardBody>
        {loadError(loadState) && (
          <LoadFailed what="search preferences" error={loadError(loadState)} onRetry={onRetry} />
        )}
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-bold text-black mb-1">Search Queries</label>
            <TagInput
              value={prefs.queries}
              onChange={(v) => updateField('queries', v)}
              placeholder="e.g. DevOps Engineer, SRE, Platform Engineer"
            />
            <p className="text-xs text-stone-400 mt-1 font-mono">Press Enter to add a keyword</p>
          </div>

          <div>
            <label className="block text-sm font-bold text-black mb-1">Locations</label>
            <TagInput
              value={prefs.locations}
              onChange={(v) => updateField('locations', v)}
              placeholder="e.g. Dublin, Remote, London"
            />
            <p className="text-xs text-stone-400 mt-1 font-mono">Press Enter to add a location</p>
          </div>

          <div>
            <label className="block text-sm font-bold text-black mb-2">Experience Level</label>
            <div className="flex flex-wrap gap-3">
              {[
                { value: 'entry_level', label: 'Entry Level' },
                { value: 'mid_level', label: 'Mid Level' },
                { value: 'senior', label: 'Senior' },
              ].map(({ value, label }) => (
                <label key={value} className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={prefs.experience_levels.includes(value)}
                    onChange={() => toggleLevel(value)}
                    className="w-4 h-4 border-2 border-black accent-black"
                  />
                  <span className="text-sm font-bold text-black">{label}</span>
                </label>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-bold text-black mb-1">Days Back</label>
              <Input
                type="number"
                min={1}
                max={30}
                value={prefs.days_back}
                onChange={(e) => updateField('days_back', parseInt(e.target.value) || 7)}
              />
              <p className="text-xs text-stone-400 mt-1 font-mono">How far back to search</p>
            </div>
            <div>
              <label className="block text-sm font-bold text-black mb-1">Max Jobs per Run</label>
              <Input
                type="number"
                min={1}
                max={100}
                value={prefs.max_jobs_per_run}
                onChange={(e) => updateField('max_jobs_per_run', parseInt(e.target.value) || 15)}
              />
              <p className="text-xs text-stone-400 mt-1 font-mono">Limit per pipeline run</p>
            </div>
            <div>
              <label className="block text-sm font-bold text-black mb-1">
                Min Match Score: <span className="text-yellow-dark font-mono">{prefs.min_match_score}</span>
              </label>
              <input
                type="range"
                min={0}
                max={100}
                value={prefs.min_match_score}
                onChange={(e) => updateField('min_match_score', parseInt(e.target.value))}
                className="w-full h-2 bg-stone-200 rounded appearance-none cursor-pointer accent-black"
              />
              <div className="flex justify-between text-xs text-stone-400 mt-1 font-mono">
                <span>0</span>
                <span>100</span>
              </div>
            </div>
          </div>
        </div>

        <div className="mt-5 flex items-center gap-3">
          <Button onClick={handleSave} disabled={saving || !isLoaded(loadState)}>
            {saving && <span className="spinner" />}
            Save Changes
          </Button>
          <StatusMessage status={status} />
        </div>
      </CardBody>
    </Card>
  )
}

const JOB_SOURCES = [
  { id: 'linkedin', label: 'LinkedIn', enabled: true },
  { id: 'indeed', label: 'Indeed', enabled: true },
  { id: 'greenhouse', label: 'Greenhouse API', enabled: true },
  { id: 'ashby', label: 'Ashby API', enabled: true },
  { id: 'irish_portals', label: 'Irish Portals', enabled: true },
  { id: 'hn', label: 'HN Hiring', enabled: true },
  { id: 'yc', label: 'YC / WATS', enabled: true },
  { id: 'glassdoor', label: 'Glassdoor', enabled: false, note: 'Needs Fargate (dormant)' },
  { id: 'adzuna', label: 'Adzuna', enabled: false, note: 'UK only — disabled' },
]

function JobSourcesSection() {
  const [enabledSources, setEnabledSources] = useState(
    JOB_SOURCES.filter((s) => s.enabled).map((s) => s.id)
  )
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState(null)

  // Third instance of the same race in this file. Toggle a source before
  // /api/search-config resolves and the response replaces the whole list,
  // silently undoing the toggle.
  const sourcesDirty = useRef(false)

  // Same rule as the profile: if the stored list never arrived, the toggles
  // show the hard-coded defaults, and saving them would replace the real list.
  const [loadState, setLoadState] = useState('loading')
  const load = useCallback(() => {
    apiGet('/api/search-config')
      .then((data) => {
        setLoadState('loaded')
        if (sourcesDirty.current) return
        if (data.enabled_sources && Array.isArray(data.enabled_sources)) {
          setEnabledSources(data.enabled_sources)
        }
      })
      .catch((e) => {
        console.warn('Failed to load enabled sources:', e)
        setLoadState({ error: e?.message || 'request failed' })
      })
  }, [])

  useEffect(() => { load() }, [load])

  function toggleSource(sourceId) {
    sourcesDirty.current = true
    setEnabledSources((prev) =>
      prev.includes(sourceId)
        ? prev.filter((s) => s !== sourceId)
        : [...prev, sourceId]
    )
  }

  async function handleSave() {
    if (!isLoaded(loadState)) return
    setSaving(true)
    setStatus(null)
    try {
      await apiPut('/api/search-config', { enabled_sources: enabledSources })
      setStatus({ type: 'success', message: 'Job sources saved.' })
    } catch (e) {
      setStatus({ type: 'error', message: `Save failed: ${e.message}` })
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <div>
          <h3 className="text-base font-heading font-bold text-black">Job Sources</h3>
          <p className="text-sm text-stone-500 mt-0.5">Toggle which job boards the pipeline scrapes.</p>
        </div>
      </CardHeader>
      <CardBody>
        {loadError(loadState) && (
          <LoadFailed what="job sources" error={loadError(loadState)} onRetry={() => { setLoadState('loading'); load() }} />
        )}
        <div className="space-y-3">
          {JOB_SOURCES.map((source) => {
            const active = enabledSources.includes(source.id)
            const disabled = source.enabled === false
            return (
              <div
                key={source.id}
                className={`flex items-center justify-between px-4 py-3 border-2 transition-colors ${
                  disabled ? 'border-stone-200 bg-stone-100 opacity-60' :
                  active ? 'border-black bg-white' : 'border-stone-300 bg-stone-50'
                }`}
              >
                <div className="flex items-center gap-3">
                  <span className={`w-2 h-2 ${disabled ? 'bg-error' : active ? 'bg-success' : 'bg-stone-300'}`} />
                  <span className={`text-sm font-bold ${disabled ? 'text-stone-400 line-through' : active ? 'text-black' : 'text-stone-400'}`}>
                    {source.label}
                  </span>
                  {source.note && (
                    <span className="text-[10px] text-stone-400 font-mono">{source.note}</span>
                  )}
                </div>
                <button
                  onClick={() => !disabled && toggleSource(source.id)}
                  disabled={disabled}
                  className={`relative inline-flex h-6 w-11 items-center border-2 transition-colors ${
                    disabled ? 'border-stone-300 bg-stone-200 cursor-not-allowed' :
                    active ? 'border-black bg-black cursor-pointer' : 'border-black bg-white cursor-pointer'
                  }`}
                >
                  <span
                    className={`inline-block h-4 w-4 transform transition-transform ${
                      disabled ? 'translate-x-[2px] bg-stone-400' :
                      active ? 'translate-x-[22px] bg-yellow' : 'translate-x-[2px] bg-stone-300'
                    }`}
                  />
                </button>
              </div>
            )
          })}
        </div>

        <div className="mt-5 flex items-center gap-3">
          <Button onClick={handleSave} disabled={saving || !isLoaded(loadState)}>
            {saving && <span className="spinner" />}
            Save Sources
          </Button>
          <StatusMessage status={status} />
        </div>
      </CardBody>
    </Card>
  )
}

export default function Settings() {
  const { user, loading } = useAuth()

  const [profile, setProfile] = useState({
    name: '',
    email: '',
    phone: '',
    location: '',
    github_url: '',
    linkedin_url: '',
    website: '',
    visa_status: '',
    work_authorizations: [],
    salary_expectation_notes: '',
    notice_period_text: '',
  })

  // Set the moment the user edits preferences, so a late /api/search-config
  // response cannot revert them. A ref rather than state: it must be readable
  // inside the fetch callback without re-running the effect.
  const prefsDirty = useRef(false)
  const editPrefs = (updater) => {
    prefsDirty.current = true
    setPrefs(updater)
  }

  const [prefs, setPrefs] = useState({
    queries: [],
    locations: [],
    experience_levels: ['entry_level'],
    days_back: 7,
    max_jobs_per_run: 15,
    min_match_score: 60,
  })

  // Per-section load state. A section is saveable only once its GET has
  // succeeded: before that the form holds blanks/defaults, not the user's data.
  const [profileLoad, setProfileLoad] = useState('loading')
  const [prefsLoad, setPrefsLoad] = useState('loading')

  const loadProfile = useCallback(() => {
    if (!user) return
    apiGet('/api/profile')
      .then((data) => {
        // Hydration FILLS BLANKS; it must never overwrite. Every field above
        // starts as '' (or []), so "still blank" is an exact test for "the user
        // has not typed here" and no dirty-tracking is needed.
        //
        // This used to be `data.full_name ?? prev.name`, which let a
        // late-resolving fetch clobber whatever had been typed in the meantime.
        // Open Settings on a slow connection, start typing your name, and the
        // response lands and silently reverts it -- then Save submits the old
        // value. It is the same shape as the job-edit defect in #165: the form
        // shows one thing and the request carries another.
        //
        // Caught by tests/e2e/test_critical_journeys.py::TestSettings::
        // test_profile_update, which failed on main at e82c2db and 5bc82c4 with
        // `assert 'E2E Test User' == 'Renamed User'` -- the PUT carrying the
        // pre-typed name. It passes locally (3/3) and fails on the slower
        // runner, which is what a load-sensitive race looks like rather than a
        // flake.
        const keep = (typed, incoming) =>
          (typed === '' || typed === undefined || typed === null)
            ? (incoming ?? typed ?? '')
            : typed
        setProfile((prev) => ({
          ...prev,
          name: keep(prev.name, data.full_name),
          email: keep(prev.email, data.email ?? user.email),
          phone: keep(prev.phone, data.phone),
          location: keep(prev.location, data.location),
          github_url: keep(prev.github_url, data.github_url),
          linkedin_url: keep(prev.linkedin_url, data.linkedin_url),
          website: keep(prev.website, data.website),
          visa_status: keep(prev.visa_status, data.visa_status),
          // Array field: only hydrate while the user has added no rows, for the
          // same reason. Replacing a half-filled list mid-edit loses the rows.
          work_authorizations:
            (prev.work_authorizations || []).length === 0 && data.work_authorizations
              ? Object.entries(data.work_authorizations).map(([country, status]) => ({ country, status }))
              : prev.work_authorizations,
          salary_expectation_notes: keep(prev.salary_expectation_notes, data.salary_expectation_notes),
          notice_period_text: keep(prev.notice_period_text, data.notice_period_text),
        }))
        setProfileLoad('loaded')
      })
      .catch((e) => {
        console.warn('Failed to load profile:', e)
        setProfileLoad({ error: e?.message || 'request failed' })
      })
  }, [user])

  const loadPrefs = useCallback(() => {
    if (!user) return
    apiGet('/api/search-config')
      .then((data) => {
        setPrefsLoad('loaded')
        // Same race as the profile hydration above, sibling state. `prefs`
        // defaults are NOT blank (min_match_score 60, days_back 7,
        // max_jobs_per_run 15), so the "fill blanks" test that works for the
        // profile cannot tell a default from a deliberate choice of the same
        // value. A dirty flag can, and works whatever the defaults are.
        //
        // Caught by tests/e2e/.../TestSettings::test_search_config_update:
        // `assert 60 == 70` -- the user moves the score to 70, this response
        // lands, and Save submits the default.
        if (prefsDirty.current) return
        setPrefs((prev) => ({
          ...prev,
          queries: data.queries ?? prev.queries,
          locations: data.locations ?? prev.locations,
          experience_levels: data.experience_levels ?? prev.experience_levels,
          days_back: data.days_back ?? prev.days_back,
          max_jobs_per_run: data.max_jobs_per_run ?? prev.max_jobs_per_run,
          min_match_score: data.min_match_score ?? prev.min_match_score,
        }))
      })
      .catch((e) => {
        console.warn('Failed to load search config:', e)
        setPrefsLoad({ error: e?.message || 'request failed' })
      })
  }, [user, setPrefs])

  useEffect(() => {
    loadProfile()
    loadPrefs()
  }, [loadProfile, loadPrefs])

  if (loading) {
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
      <h1 className="text-2xl font-heading font-bold text-black tracking-tight mb-6">Settings</h1>
      <div className="space-y-6">
        <ProfileSection profile={profile} setProfile={setProfile} loadState={profileLoad} onRetry={() => { setProfileLoad('loading'); loadProfile() }} />
        <PasswordSection />
        <ResumeSection />
        <JobSourcesSection />
        <PreferencesSection prefs={prefs} setPrefs={editPrefs} loadState={prefsLoad} onRetry={() => { setPrefsLoad('loading'); loadPrefs() }} />
      </div>
      {/* On mobile, "More" lands here and the Sidebar's Account links are
          hidden, so the data pages need a way in from this page too. Plain
          anchors: Settings is also rendered outside a router (its tests). */}
      <footer className="mt-8 pt-4 border-t-2 border-black flex flex-wrap gap-4 text-sm font-bold">
        <a href="/data-export" className="underline hover:no-underline">Data &amp; Privacy (export or delete my data)</a>
        <a href="/privacy" className="underline hover:no-underline">Privacy Policy</a>
      </footer>
    </div>
  )
}
