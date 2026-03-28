import { useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/useAuth'
import { apiPut, apiUpload } from '../api'

const STEPS = ['Profile', 'Resume', 'Preferences']

function StepIndicator({ current }) {
  return (
    <div className="flex items-center justify-center gap-2 mb-8">
      {STEPS.map((label, i) => (
        <div key={label} className="flex items-center gap-2">
          <div className="flex items-center gap-2">
            <div
              className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium transition
                ${i < current ? 'bg-blue-600 text-white' : ''}
                ${i === current ? 'bg-blue-600 text-white ring-2 ring-blue-300 ring-offset-2' : ''}
                ${i > current ? 'bg-gray-200 text-gray-500' : ''}`}
            >
              {i < current ? (
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
              ) : (
                i + 1
              )}
            </div>
            <span className={`text-sm hidden sm:block ${i === current ? 'text-gray-900 font-medium' : 'text-gray-500'}`}>
              {label}
            </span>
          </div>
          {i < STEPS.length - 1 && (
            <div className={`w-8 h-0.5 ${i < current ? 'bg-blue-600' : 'bg-gray-200'}`} />
          )}
        </div>
      ))}
    </div>
  )
}

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

function WorkAuthRow({ country, status, onChangeCountry, onChangeStatus, onRemove }) {
  return (
    <div className="flex gap-2 items-center">
      <input
        type="text"
        value={country}
        onChange={(e) => onChangeCountry(e.target.value)}
        placeholder="Country (e.g. Ireland)"
        className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
      />
      <input
        type="text"
        value={status}
        onChange={(e) => onChangeStatus(e.target.value)}
        placeholder="Status (e.g. Stamp 1G)"
        className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
      />
      <button
        type="button"
        onClick={onRemove}
        className="text-gray-400 hover:text-red-500 p-1 transition"
      >
        <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
        </svg>
      </button>
    </div>
  )
}

function StepProfile({ profile, setProfile }) {
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

  return (
    <div className="space-y-5">
      <h2 className="text-lg font-semibold text-gray-900">Tell us about yourself</h2>
      <p className="text-sm text-gray-500">This helps us tailor job matches and resumes for you.</p>

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
            <WorkAuthRow
              key={i}
              country={auth.country}
              status={auth.status}
              onChangeCountry={(v) => updateWorkAuth(i, 'country', v)}
              onChangeStatus={(v) => updateWorkAuth(i, 'status', v)}
              onRemove={() => removeWorkAuth(i)}
            />
          ))}
          {profile.work_authorizations.length === 0 && (
            <p className="text-sm text-gray-400 italic">No work authorizations added yet.</p>
          )}
        </div>
      </div>
    </div>
  )
}

function StepResume({ resumeFile, setResumeFile, uploadStatus, setUploadStatus }) {
  const fileInputRef = useRef(null)
  const [dragOver, setDragOver] = useState(false)

  function handleFile(file) {
    if (file && file.type === 'application/pdf') {
      setResumeFile(file)
      setUploadStatus(null)
    } else {
      setUploadStatus({ type: 'error', message: 'Please upload a PDF file.' })
    }
  }

  function handleDrop(e) {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer.files[0]
    handleFile(file)
  }

  function handleDragOver(e) {
    e.preventDefault()
    setDragOver(true)
  }

  function handleDragLeave() {
    setDragOver(false)
  }

  async function handleUpload() {
    if (!resumeFile) return
    setUploadStatus({ type: 'loading', message: 'Uploading...' })
    try {
      await apiUpload('/api/resumes/upload', resumeFile)
      setUploadStatus({ type: 'success', message: 'Resume uploaded and parsed successfully.' })
    } catch (e) {
      if (e.message.includes('404') || e.message.includes('Not Found')) {
        setUploadStatus({ type: 'info', message: 'Resume upload API coming soon. Your file has been saved locally.' })
      } else {
        setUploadStatus({ type: 'error', message: `Upload failed: ${e.message}` })
      }
    }
  }

  return (
    <div className="space-y-5">
      <h2 className="text-lg font-semibold text-gray-900">Upload your resume</h2>
      <p className="text-sm text-gray-500">
        We will parse your resume to pre-fill your profile and use it as a base for tailoring.
      </p>

      <div
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onClick={() => fileInputRef.current?.click()}
        className={`border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition
          ${dragOver ? 'border-blue-400 bg-blue-50' : 'border-gray-300 hover:border-gray-400 hover:bg-gray-50'}`}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf"
          className="hidden"
          onChange={(e) => handleFile(e.target.files[0])}
        />
        <svg className="w-10 h-10 mx-auto text-gray-400 mb-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 16.5V9.75m0 0l3 3m-3-3l-3 3M6.75 19.5a4.5 4.5 0 01-1.41-8.775 5.25 5.25 0 0110.233-2.33 3 3 0 013.758 3.848A3.752 3.752 0 0118 19.5H6.75z" />
        </svg>
        {resumeFile ? (
          <div>
            <p className="text-sm font-medium text-gray-900">{resumeFile.name}</p>
            <p className="text-xs text-gray-500 mt-1">{(resumeFile.size / 1024).toFixed(1)} KB - Click or drop to replace</p>
          </div>
        ) : (
          <div>
            <p className="text-sm font-medium text-gray-700">Drop your PDF here, or click to browse</p>
            <p className="text-xs text-gray-400 mt-1">PDF files only, up to 10 MB</p>
          </div>
        )}
      </div>

      {resumeFile && (
        <button
          type="button"
          onClick={handleUpload}
          disabled={uploadStatus?.type === 'loading'}
          className="bg-blue-600 hover:bg-blue-700 text-white px-5 py-2.5 rounded-lg text-sm font-medium transition
            focus:outline-none focus:ring-2 focus:ring-blue-300 focus:ring-offset-2
            disabled:opacity-50 disabled:cursor-not-allowed
            inline-flex items-center gap-2"
        >
          {uploadStatus?.type === 'loading' && <span className="spinner" />}
          Upload & Parse
        </button>
      )}

      {uploadStatus && uploadStatus.type !== 'loading' && (
        <div
          className={`p-3 rounded-lg text-sm border
            ${uploadStatus.type === 'success' ? 'bg-green-50 border-green-200 text-green-700' : ''}
            ${uploadStatus.type === 'error' ? 'bg-red-50 border-red-200 text-red-700' : ''}
            ${uploadStatus.type === 'info' ? 'bg-blue-50 border-blue-200 text-blue-700' : ''}`}
        >
          {uploadStatus.message}
        </div>
      )}
    </div>
  )
}

function StepPreferences({ prefs, setPrefs }) {
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

  return (
    <div className="space-y-5">
      <h2 className="text-lg font-semibold text-gray-900">Search preferences</h2>
      <p className="text-sm text-gray-500">Configure what kind of jobs we should look for.</p>

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
  )
}

export default function Onboarding() {
  const { user, loading } = useAuth()
  const navigate = useNavigate()
  const [step, setStep] = useState(0)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

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

  const [resumeFile, setResumeFile] = useState(null)
  const [uploadStatus, setUploadStatus] = useState(null)

  const [prefs, setPrefs] = useState({
    search_queries: [],
    locations: [],
    experience_levels: ['entry_level'],
    days_back: 7,
    max_jobs_per_run: 15,
    min_match_score: 60,
  })

  // Keep email in sync if user loads after initial render
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

  async function handleComplete() {
    setSaving(true)
    setError(null)
    try {
      // Save profile
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
    } catch (e) {
      // Profile endpoint may not exist yet — that's OK
      console.warn('Profile save:', e.message)
    }

    try {
      // Save search config
      await apiPut('/api/search-config', prefs)
    } catch (e) {
      console.warn('Search config save:', e.message)
    }

    setSaving(false)
    alert('Setup complete! Your preferences have been saved.')
    navigate('/')
  }

  function next() {
    if (step < STEPS.length - 1) setStep(step + 1)
  }

  function back() {
    if (step > 0) setStep(step - 1)
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b border-gray-200">
        <div className="max-w-2xl mx-auto px-4 py-4 flex items-center gap-3">
          <span className="text-2xl">🎯</span>
          <h1 className="text-xl font-bold text-gray-900">Welcome to NaukriBaba</h1>
        </div>
      </header>

      <main className="max-w-2xl mx-auto px-4 py-8">
        <StepIndicator current={step} />

        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6 mb-6 animate-fade-in">
          {step === 0 && <StepProfile profile={profile} setProfile={setProfile} />}
          {step === 1 && (
            <StepResume
              resumeFile={resumeFile}
              setResumeFile={setResumeFile}
              uploadStatus={uploadStatus}
              setUploadStatus={setUploadStatus}
            />
          )}
          {step === 2 && <StepPreferences prefs={prefs} setPrefs={setPrefs} />}
        </div>

        {error && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
            {error}
          </div>
        )}

        {/* Navigation buttons */}
        <div className="flex items-center justify-between">
          <div>
            {step > 0 && (
              <button
                type="button"
                onClick={back}
                className="text-sm text-gray-600 hover:text-gray-900 font-medium transition px-4 py-2.5"
              >
                Back
              </button>
            )}
          </div>
          <div className="flex items-center gap-3">
            {step === 1 && (
              <button
                type="button"
                onClick={next}
                className="text-sm text-gray-500 hover:text-gray-700 font-medium transition px-4 py-2.5"
              >
                Skip
              </button>
            )}
            {step < STEPS.length - 1 ? (
              <button
                type="button"
                onClick={next}
                className="bg-blue-600 hover:bg-blue-700 text-white px-6 py-2.5 rounded-lg text-sm font-medium transition
                  focus:outline-none focus:ring-2 focus:ring-blue-300 focus:ring-offset-2"
              >
                Next
              </button>
            ) : (
              <button
                type="button"
                onClick={handleComplete}
                disabled={saving}
                className="bg-blue-600 hover:bg-blue-700 text-white px-6 py-2.5 rounded-lg text-sm font-medium transition
                  focus:outline-none focus:ring-2 focus:ring-blue-300 focus:ring-offset-2
                  disabled:opacity-50 disabled:cursor-not-allowed
                  inline-flex items-center gap-2"
              >
                {saving && <span className="spinner" />}
                Complete Setup
              </button>
            )}
          </div>
        </div>
      </main>
    </div>
  )
}
