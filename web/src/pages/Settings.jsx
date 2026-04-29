import { useState, useEffect, useRef } from 'react'
import { useAuth } from '../auth/useAuth'
import { apiGet, apiPut, apiUpload, apiDelete } from '../api'
import Card, { CardHeader, CardBody } from '../components/ui/Card'
import Input from '../components/ui/Input'
import Button from '../components/ui/Button'
import LoginPage from './LoginPage'

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

function ProfileSection({ profile, setProfile }) {
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
    setSaving(true)
    setStatus(null)
    try {
      const workAuthObj = {}
      for (const auth of profile.work_authorizations) {
        if (auth.country.trim()) {
          workAuthObj[auth.country.trim()] = auth.status.trim()
        }
      }
      await apiPut('/api/profile', {
        ...profile,
        work_authorizations: workAuthObj,
      })
      setStatus({ type: 'success', message: 'Profile saved.' })
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

          <Input label="Notice Period" value={profile.notice_period_text || ''}
            onChange={e => updateField('notice_period_text', e.target.value)}
            placeholder="e.g. 2 weeks, 1 month" />
        </div>

        <div className="mt-5 flex items-center gap-3">
          <Button onClick={handleSave} disabled={saving}>
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

function ResumeSection() {
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

  async function handleDelete(id) {
    try {
      await apiDelete(`/api/resumes/${id}`)
      setResumes((prev) => prev.filter((r) => r.id !== id))
    } catch (e) {
      console.warn('Failed to delete resume:', e)
    }
  }

  function handleFile(file) {
    if (file && file.type === 'application/pdf') {
      setResumeFile(file)
      setUploadStatus(null)
    } else if (file) {
      setUploadStatus({ type: 'error', message: 'Please upload a PDF file.' })
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
      await apiUpload('/api/resumes/upload', resumeFile)
      setUploadStatus({ type: 'success', message: 'Resume uploaded and parsed successfully.' })
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
        {resumes.length > 0 ? (
          <ul className="mb-4 divide-y divide-stone-200 border-2 border-black overflow-hidden">
            {resumes.map((resume) => (
              <li key={resume.id} className="flex items-center justify-between px-4 py-3 bg-white hover:bg-yellow-light transition-colors">
                <div>
                  <p className="text-sm font-bold text-black">{resume.label || resume.filename || resume.name || `Resume ${resume.id}`}</p>
                  {resume.uploaded_at && (
                    <p className="text-xs text-stone-400 mt-0.5 font-mono">
                      {new Date(resume.uploaded_at).toLocaleDateString()}
                    </p>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => handleDelete(resume.id)}
                  className="text-stone-400 hover:text-error p-1.5 transition"
                  title="Delete resume"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                </button>
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
            accept=".pdf"
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

function PreferencesSection({ prefs, setPrefs }) {
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
          <Button onClick={handleSave} disabled={saving}>
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

  useEffect(() => {
    apiGet('/api/search-config')
      .then((data) => {
        if (data.enabled_sources && Array.isArray(data.enabled_sources)) {
          setEnabledSources(data.enabled_sources)
        }
      })
      .catch((e) => console.warn('Failed to load enabled sources:', e))
  }, [])

  function toggleSource(sourceId) {
    setEnabledSources((prev) =>
      prev.includes(sourceId)
        ? prev.filter((s) => s !== sourceId)
        : [...prev, sourceId]
    )
  }

  async function handleSave() {
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
          <Button onClick={handleSave} disabled={saving}>
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

  const [prefs, setPrefs] = useState({
    queries: [],
    locations: [],
    experience_levels: ['entry_level'],
    days_back: 7,
    max_jobs_per_run: 15,
    min_match_score: 60,
  })

  useEffect(() => {
    if (!user) return
    apiGet('/api/profile')
      .then((data) => {
        setProfile((prev) => ({
          ...prev,
          name: data.full_name ?? prev.name,
          email: data.email ?? user.email ?? prev.email,
          phone: data.phone ?? prev.phone,
          location: data.location ?? prev.location,
          github_url: data.github_url ?? prev.github_url,
          linkedin_url: data.linkedin_url ?? prev.linkedin_url,
          website: data.website ?? prev.website,
          visa_status: data.visa_status ?? prev.visa_status,
          work_authorizations: data.work_authorizations
            ? Object.entries(data.work_authorizations).map(([country, status]) => ({ country, status }))
            : prev.work_authorizations,
          salary_expectation_notes: data.salary_expectation_notes ?? prev.salary_expectation_notes,
          notice_period_text: data.notice_period_text ?? prev.notice_period_text,
        }))
      })
      .catch((e) => console.warn('Failed to load profile:', e))

    apiGet('/api/search-config')
      .then((data) => {
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
      .catch((e) => console.warn('Failed to load search config:', e))
  }, [user])

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
        <ProfileSection profile={profile} setProfile={setProfile} />
        <PasswordSection />
        <ResumeSection />
        <JobSourcesSection />
        <PreferencesSection prefs={prefs} setPrefs={setPrefs} />
      </div>
    </div>
  )
}
