import { useState, useRef } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../auth/useAuth'
import { apiPut, apiUpload } from '../api'

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
    <div className="w-full border border-gray-300 rounded-lg px-3 py-2 flex flex-wrap gap-2 focus-within:ring-2 focus-within:ring-blue-500 focus-within:border-blue-500">
      {value.map((tag) => (
        <span
          key={tag}
          className="inline-flex items-center gap-1 bg-blue-50 text-blue-700 text-sm px-2.5 py-0.5 rounded-full"
        >
          {tag}
          <button
            type="button"
            onClick={() => removeTag(tag)}
            className="text-blue-400 hover:text-blue-600"
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
        className="flex-1 min-w-[120px] outline-none text-sm placeholder:text-gray-400"
      />
    </div>
  )
}

function SectionHeader({ title, description }) {
  return (
    <div className="mb-4">
      <h3 className="text-base font-semibold text-gray-900">{title}</h3>
      {description && <p className="text-sm text-gray-500 mt-0.5">{description}</p>}
    </div>
  )
}

function SaveButton({ onClick, saving, label = 'Save Changes' }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={saving}
      className="bg-blue-600 hover:bg-blue-700 text-white px-5 py-2 rounded-lg text-sm font-medium transition
        focus:outline-none focus:ring-2 focus:ring-blue-300 focus:ring-offset-2
        disabled:opacity-50 disabled:cursor-not-allowed
        inline-flex items-center gap-2"
    >
      {saving && <span className="spinner" />}
      {label}
    </button>
  )
}

function StatusMessage({ status }) {
  if (!status) return null
  const styles = {
    success: 'bg-green-50 border-green-200 text-green-700',
    error: 'bg-red-50 border-red-200 text-red-700',
    info: 'bg-blue-50 border-blue-200 text-blue-700',
  }
  return (
    <div className={`mt-3 p-3 rounded-lg text-sm border ${styles[status.type] || styles.info}`}>
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
      if (e.message.includes('404') || e.message.includes('Not Found')) {
        setStatus({ type: 'info', message: 'Profile API coming soon. Changes saved locally.' })
      } else {
        setStatus({ type: 'error', message: `Save failed: ${e.message}` })
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
      <SectionHeader title="Profile" description="Your personal and contact information." />

      <div className="space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Full Name</label>
            <input
              type="text"
              value={profile.name}
              onChange={(e) => updateField('name', e.target.value)}
              placeholder="Utkarsh Singh"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 placeholder:text-gray-400"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Email</label>
            <input
              type="email"
              value={profile.email}
              disabled
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm bg-gray-50 text-gray-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Phone</label>
            <input
              type="tel"
              value={profile.phone}
              onChange={(e) => updateField('phone', e.target.value)}
              placeholder="+353 85 123 4567"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 placeholder:text-gray-400"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Location</label>
            <input
              type="text"
              value={profile.location}
              onChange={(e) => updateField('location', e.target.value)}
              placeholder="Dublin, Ireland"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 placeholder:text-gray-400"
            />
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">GitHub URL</label>
            <input
              type="url"
              value={profile.github_url}
              onChange={(e) => updateField('github_url', e.target.value)}
              placeholder="https://github.com/username"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 placeholder:text-gray-400"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">LinkedIn URL</label>
            <input
              type="url"
              value={profile.linkedin_url}
              onChange={(e) => updateField('linkedin_url', e.target.value)}
              placeholder="https://linkedin.com/in/username"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 placeholder:text-gray-400"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Website</label>
            <input
              type="url"
              value={profile.website}
              onChange={(e) => updateField('website', e.target.value)}
              placeholder="https://yoursite.com"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 placeholder:text-gray-400"
            />
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Visa Status</label>
          <input
            type="text"
            value={profile.visa_status}
            onChange={(e) => updateField('visa_status', e.target.value)}
            placeholder="e.g. Stamp 1G, EU Citizen, H-1B"
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 placeholder:text-gray-400"
          />
        </div>

        <div>
          <div className="flex items-center justify-between mb-2">
            <label className="block text-sm font-medium text-gray-700">Work Authorizations</label>
            <button
              type="button"
              onClick={addWorkAuth}
              className="text-sm text-blue-600 hover:text-blue-700 font-medium"
            >
              + Add country
            </button>
          </div>
          <div className="space-y-2">
            {profile.work_authorizations.map((auth, i) => (
              <div key={i} className="flex gap-2 items-center">
                <input
                  type="text"
                  value={auth.country}
                  onChange={(e) => updateWorkAuth(i, 'country', e.target.value)}
                  placeholder="Country (e.g. Ireland)"
                  className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                />
                <input
                  type="text"
                  value={auth.status}
                  onChange={(e) => updateWorkAuth(i, 'status', e.target.value)}
                  placeholder="Status (e.g. Stamp 1G)"
                  className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                />
                <button
                  type="button"
                  onClick={() => removeWorkAuth(i)}
                  className="text-gray-400 hover:text-red-500 p-1 transition"
                >
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                </button>
              </div>
            ))}
            {profile.work_authorizations.length === 0 && (
              <p className="text-sm text-gray-400 italic">No work authorizations added yet.</p>
            )}
          </div>
        </div>
      </div>

      <div className="mt-5 flex items-center gap-3">
        <SaveButton onClick={handleSave} saving={saving} />
        <StatusMessage status={status} />
      </div>
    </div>
  )
}

function ResumeSection() {
  const fileInputRef = useRef(null)
  const [resumeFile, setResumeFile] = useState(null)
  const [uploadStatus, setUploadStatus] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)

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
    } catch (e) {
      if (e.message.includes('404') || e.message.includes('Not Found')) {
        setUploadStatus({ type: 'info', message: 'Resume upload API coming soon. Your file has been saved locally.' })
      } else {
        setUploadStatus({ type: 'error', message: `Upload failed: ${e.message}` })
      }
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
      <SectionHeader title="Resumes" description="Upload and manage your resume files." />

      <div className="text-sm text-gray-500 mb-4 p-3 bg-gray-50 rounded-lg border border-gray-100">
        Resume listing will appear here once the API is connected.
      </div>

      <div
        onDrop={handleDrop}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onClick={() => fileInputRef.current?.click()}
        className={`border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition
          ${dragOver ? 'border-blue-400 bg-blue-50' : 'border-gray-300 hover:border-gray-400 hover:bg-gray-50'}`}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf"
          className="hidden"
          onChange={(e) => handleFile(e.target.files[0])}
        />
        <svg className="w-8 h-8 mx-auto text-gray-400 mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 16.5V9.75m0 0l3 3m-3-3l-3 3M6.75 19.5a4.5 4.5 0 01-1.41-8.775 5.25 5.25 0 0110.233-2.33 3 3 0 013.758 3.848A3.752 3.752 0 0118 19.5H6.75z" />
        </svg>
        {resumeFile ? (
          <p className="text-sm font-medium text-gray-900">{resumeFile.name} ({(resumeFile.size / 1024).toFixed(1)} KB)</p>
        ) : (
          <p className="text-sm text-gray-500">Drop a PDF here, or click to browse</p>
        )}
      </div>

      {resumeFile && (
        <div className="mt-4">
          <SaveButton onClick={handleUpload} saving={uploading} label="Upload & Parse" />
        </div>
      )}

      <StatusMessage status={uploadStatus} />
    </div>
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
      if (e.message.includes('404') || e.message.includes('Not Found')) {
        setStatus({ type: 'info', message: 'Search config API coming soon. Changes saved locally.' })
      } else {
        setStatus({ type: 'error', message: `Save failed: ${e.message}` })
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
      <SectionHeader title="Search Preferences" description="Configure your automated job search." />

      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Search Queries</label>
          <TagInput
            value={prefs.search_queries}
            onChange={(v) => updateField('search_queries', v)}
            placeholder="e.g. DevOps Engineer, SRE, Platform Engineer"
          />
          <p className="text-xs text-gray-400 mt-1">Press Enter to add a keyword</p>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Locations</label>
          <TagInput
            value={prefs.locations}
            onChange={(v) => updateField('locations', v)}
            placeholder="e.g. Dublin, Remote, London"
          />
          <p className="text-xs text-gray-400 mt-1">Press Enter to add a location</p>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">Experience Level</label>
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
                  className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                />
                <span className="text-sm text-gray-700">{label}</span>
              </label>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Days Back</label>
            <input
              type="number"
              min={1}
              max={30}
              value={prefs.days_back}
              onChange={(e) => updateField('days_back', parseInt(e.target.value) || 7)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
            />
            <p className="text-xs text-gray-400 mt-1">How far back to search</p>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Max Jobs per Run</label>
            <input
              type="number"
              min={1}
              max={100}
              value={prefs.max_jobs_per_run}
              onChange={(e) => updateField('max_jobs_per_run', parseInt(e.target.value) || 15)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
            />
            <p className="text-xs text-gray-400 mt-1">Limit per pipeline run</p>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Min Match Score: <span className="text-blue-600">{prefs.min_match_score}</span>
            </label>
            <input
              type="range"
              min={0}
              max={100}
              value={prefs.min_match_score}
              onChange={(e) => updateField('min_match_score', parseInt(e.target.value))}
              className="w-full h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-blue-600"
            />
            <div className="flex justify-between text-xs text-gray-400 mt-1">
              <span>0</span>
              <span>100</span>
            </div>
          </div>
        </div>
      </div>

      <div className="mt-5 flex items-center gap-3">
        <SaveButton onClick={handleSave} saving={saving} />
        <StatusMessage status={status} />
      </div>
    </div>
  )
}

export default function Settings() {
  const { user, loading, signOut } = useAuth()

  const [profile, setProfile] = useState({
    name: '',
    email: user?.email || '',
    phone: '',
    location: '',
    github_url: '',
    linkedin_url: '',
    website: '',
    visa_status: '',
    work_authorizations: [],
  })

  const [prefs, setPrefs] = useState({
    search_queries: [],
    locations: [],
    experience_levels: ['entry_level'],
    days_back: 7,
    max_jobs_per_run: 15,
    min_match_score: 60,
  })

  // Keep email in sync
  if (user?.email && !profile.email) {
    setProfile((prev) => ({ ...prev, email: user.email }))
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="text-gray-500 text-sm">Loading...</div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-4xl mx-auto px-4 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-2xl">🎯</span>
            <h1 className="text-xl font-bold text-gray-900">Settings</h1>
          </div>
          <div className="flex items-center gap-4">
            <Link
              to="/"
              className="text-sm text-gray-600 hover:text-gray-900 font-medium transition"
            >
              Tailor
            </Link>
            <Link
              to="/dashboard"
              className="text-sm text-gray-600 hover:text-gray-900 font-medium transition"
            >
              Dashboard
            </Link>
            <span className="text-sm text-gray-500 hidden sm:block">{user?.email}</span>
            <button
              onClick={signOut}
              className="text-sm text-gray-500 hover:text-gray-700 font-medium transition"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-4 py-8 space-y-6">
        <ProfileSection profile={profile} setProfile={setProfile} />
        <ResumeSection />
        <PreferencesSection prefs={prefs} setPrefs={setPrefs} />
      </main>

      {/* Footer */}
      <footer className="border-t border-gray-200 mt-12">
        <div className="max-w-4xl mx-auto px-4 py-4 text-center text-xs text-gray-400">
          Built by Utkarsh Singh — FastAPI + React + Tailwind
        </div>
      </footer>
    </div>
  )
}
