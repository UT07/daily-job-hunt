import { useState, useRef, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/useAuth'
import { useUserProfile } from '../hooks/useUserProfile'
import { apiPut, apiUpload, apiGet } from '../api'
import Input, { Textarea, Select } from '../components/ui/Input'
import Button from '../components/ui/Button'
import useApiMutation from '../hooks/useApiMutation'

// ─── Step Indicator ─────────────────────────────────────────────
function StepIndicator({ current, steps }) {
  return (
    <div className="flex items-center justify-center gap-2 mb-8">
      {steps.map((label, i) => (
        <div key={i} className="flex items-center">
          <div className={`w-8 h-8 rounded-full border-2 border-black flex items-center justify-center text-sm font-bold
            ${i < current ? 'bg-green-400' : i === current ? 'bg-yellow shadow-brutal-sm' : 'bg-cream-dark'}`}>
            {i < current ? '✓' : i + 1}
          </div>
          {i < steps.length - 1 && (
            <div className={`w-8 h-0.5 ${i < current ? 'bg-black' : 'bg-stone-300'}`} />
          )}
        </div>
      ))}
    </div>
  )
}

// ─── Reusable: Tag Input ────────────────────────────────────────
function TagInput({ value = [], onChange, placeholder }) {
  const [input, setInput] = useState('')
  function addTag() {
    const tag = input.trim()
    if (tag && !value.includes(tag)) {
      onChange([...value, tag])
      setInput('')
    }
  }
  function removeTag(tag) {
    onChange(value.filter(t => t !== tag))
  }
  function handleKeyDown(e) {
    if (e.key === 'Enter') { e.preventDefault(); addTag() }
    if (e.key === 'Backspace' && !input && value.length) {
      onChange(value.slice(0, -1))
    }
  }
  return (
    <div className="border-2 border-black rounded-lg p-2 flex flex-wrap gap-2 bg-white focus-within:shadow-brutal-yellow">
      {value.map(tag => (
        <span key={tag} className="bg-yellow px-2 py-0.5 rounded border border-black text-sm flex items-center gap-1">
          {tag}
          <button onClick={() => removeTag(tag)} className="hover:text-red-600 font-bold">×</button>
        </span>
      ))}
      <input value={input} onChange={e => setInput(e.target.value)} onKeyDown={handleKeyDown}
        placeholder={value.length ? '' : placeholder}
        className="flex-1 min-w-[120px] outline-none bg-transparent text-sm" />
    </div>
  )
}

// ─── Reusable: Work Auth Row ────────────────────────────────────
function WorkAuthRow({ country, status, onChangeCountry, onChangeStatus, onRemove }) {
  return (
    <div className="flex gap-2 items-center">
      <Input value={country} onChange={e => onChangeCountry(e.target.value)} placeholder="Country" className="flex-1" />
      <Select value={status} onChange={e => onChangeStatus(e.target.value)} className="flex-1">
        <option value="">Select status</option>
        <option value="citizen">Citizen</option>
        <option value="permanent_resident">Permanent Resident</option>
        <option value="work_visa">Work Visa</option>
        <option value="stamp_1g">Stamp 1G</option>
        <option value="stamp_4">Stamp 4</option>
        <option value="requires_sponsorship">Requires Sponsorship</option>
      </Select>
      <button onClick={onRemove} className="text-red-600 font-bold hover:bg-red-100 rounded p-1">✕</button>
    </div>
  )
}

// ─── Step 0: Welcome ────────────────────────────────────────────
function StepWelcome() {
  return (
    <div className="text-center py-8 space-y-6">
      <h2 className="text-3xl font-heading font-bold">Welcome to NaukriBaba</h2>
      <p className="text-stone-600 text-lg max-w-md mx-auto">
        Let's set up your job search pipeline. This takes about 2 minutes.
      </p>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 max-w-2xl mx-auto mt-8">
        {[
          { icon: '📄', title: 'Upload Resume', desc: 'We\'ll extract your details automatically' },
          { icon: '👤', title: 'Complete Profile', desc: 'Review and fill in any gaps' },
          { icon: '🔍', title: 'Set Preferences', desc: 'Tell us what roles you\'re looking for' },
        ].map(item => (
          <div key={item.title} className="border-2 border-black rounded-lg p-4 bg-white shadow-brutal-sm">
            <div className="text-2xl mb-2">{item.icon}</div>
            <h3 className="font-bold">{item.title}</h3>
            <p className="text-sm text-stone-500">{item.desc}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── Step 1: Resume Upload ──────────────────────────────────────
function StepResume({ resumeFile, setResumeFile, uploadStatus, setUploadStatus, onExtracted }) {
  const fileRef = useRef(null)
  const [dragOver, setDragOver] = useState(false)
  // useApiMutation surfaces the upload error message instead of just toggling
  // uploadStatus to 'error' with no detail — users couldn't tell *why*.
  const upload = useApiMutation((file) => apiUpload('/api/resumes/upload', file))

  function handleFile(file) {
    if (file && file.type === 'application/pdf') {
      setResumeFile(file)
      setUploadStatus('ready')
    }
  }
  function handleDrop(e) {
    e.preventDefault(); setDragOver(false)
    handleFile(e.dataTransfer.files[0])
  }

  async function handleUpload() {
    if (!resumeFile) return
    setUploadStatus('uploading')
    const res = await upload.run(resumeFile)
    if (!res) {
      setUploadStatus('error')
      return
    }
    setUploadStatus('done')
    if (res.extracted_profile) {
      onExtracted(res.extracted_profile)
    } else if (res.sections) {
      onExtracted({
        name: res.sections.name || '',
        email: res.sections.email || '',
        phone: res.sections.phone || '',
        location: res.sections.location || '',
        skills: res.sections.skills || '',
      })
    }
  }

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-heading font-bold">Upload Your Resume</h2>
      <p className="text-stone-500">Upload a PDF resume and we'll extract your details automatically.</p>

      <div
        className={`border-2 border-dashed rounded-lg p-12 text-center cursor-pointer transition-colors
          ${dragOver ? 'border-yellow bg-yellow-light' : 'border-stone-400 hover:border-black'}`}
        onClick={() => fileRef.current?.click()}
        onDrop={handleDrop}
        onDragOver={e => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
      >
        <input ref={fileRef} type="file" accept=".pdf" className="hidden"
          onChange={e => handleFile(e.target.files[0])} />
        <p className="text-lg font-bold">{resumeFile ? resumeFile.name : 'Drop PDF here or click to browse'}</p>
        {resumeFile && <p className="text-sm text-stone-500 mt-1">{(resumeFile.size / 1024).toFixed(0)} KB</p>}
      </div>

      {resumeFile && uploadStatus !== 'done' && (
        <Button onClick={handleUpload} loading={uploadStatus === 'uploading'}>
          {uploadStatus === 'uploading' ? 'Parsing...' : 'Upload & Parse'}
        </Button>
      )}
      {uploadStatus === 'done' && (
        <p className="text-green-700 font-bold">Resume parsed successfully! Click Next to review.</p>
      )}
      {uploadStatus === 'error' && (
        <p className="text-red-600">
          Upload failed{upload.error ? `: ${upload.error}` : ''}. Try again or skip this step.
        </p>
      )}
    </div>
  )
}

// ─── Step 2: Profile (auto-filled from CV) ──────────────────────
function StepProfile({ profile, setProfile }) {
  function updateField(field, value) {
    setProfile(prev => ({ ...prev, [field]: value }))
  }
  function updateWorkAuth(index, field, value) {
    setProfile(prev => {
      const wa = [...(prev.work_authorizations || [])]
      wa[index] = { ...wa[index], [field]: value }
      return { ...prev, work_authorizations: wa }
    })
  }
  function addWorkAuth() {
    setProfile(prev => ({
      ...prev,
      work_authorizations: [...(prev.work_authorizations || []), { country: '', status: '' }]
    }))
  }
  function removeWorkAuth(index) {
    setProfile(prev => ({
      ...prev,
      work_authorizations: prev.work_authorizations.filter((_, i) => i !== index)
    }))
  }

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-heading font-bold">Complete Your Profile</h2>
      <p className="text-stone-500">We've pre-filled what we could from your resume. Review and fill in the rest.</p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Input label="Full Name" value={profile.full_name || ''} onChange={e => updateField('full_name', e.target.value)} />
        <Input label="Email" value={profile.email || ''} disabled className="opacity-60" />
        <Input label="Phone" value={profile.phone || ''} onChange={e => updateField('phone', e.target.value)} placeholder="+353 ..." />
        <Input label="Location" value={profile.location || ''} onChange={e => updateField('location', e.target.value)} placeholder="Dublin, Ireland" />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Input label="GitHub" value={profile.github_url || ''} onChange={e => updateField('github_url', e.target.value)} placeholder="https://github.com/..." />
        <Input label="LinkedIn" value={profile.linkedin_url || ''} onChange={e => updateField('linkedin_url', e.target.value)} placeholder="https://linkedin.com/in/..." />
        <Input label="Website" value={profile.website || ''} onChange={e => updateField('website', e.target.value)} placeholder="https://..." />
      </div>

      <Input label="Visa Status" value={profile.visa_status || ''} onChange={e => updateField('visa_status', e.target.value)} placeholder="e.g. Stamp 1G, EU Citizen" />

      <div>
        <label className="block text-sm font-bold mb-2">Work Authorizations</label>
        <div className="space-y-2">
          {(profile.work_authorizations || []).map((wa, i) => (
            <WorkAuthRow key={i} country={wa.country} status={wa.status}
              onChangeCountry={v => updateWorkAuth(i, 'country', v)}
              onChangeStatus={v => updateWorkAuth(i, 'status', v)}
              onRemove={() => removeWorkAuth(i)} />
          ))}
        </div>
        <button onClick={addWorkAuth} className="mt-2 text-sm font-bold hover:underline">+ Add authorization</button>
      </div>

      <Input label="Salary Expectations" value={profile.salary_expectation_notes || ''}
        onChange={e => updateField('salary_expectation_notes', e.target.value)}
        placeholder="e.g. €70-90k base + equity" />

      <Input label="Notice Period" value={profile.notice_period_text || ''}
        onChange={e => updateField('notice_period_text', e.target.value)}
        placeholder="e.g. 2 weeks, 1 month" />

      <Textarea label="About You (for applications)" value={profile.candidate_context || ''}
        onChange={e => updateField('candidate_context', e.target.value)}
        placeholder="Brief context about your career goals, what you're looking for..."
        rows={3} />
    </div>
  )
}

// ─── Step 3: Preferences ────────────────────────────────────────
function StepPreferences({ prefs, setPrefs }) {
  function updateField(field, value) {
    setPrefs(prev => ({ ...prev, [field]: value }))
  }
  function toggleLevel(level) {
    setPrefs(prev => {
      const levels = prev.experience_levels || []
      return {
        ...prev,
        experience_levels: levels.includes(level) ? levels.filter(l => l !== level) : [...levels, level]
      }
    })
  }

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-heading font-bold">Search Preferences</h2>
      <p className="text-stone-500">Tell us what roles you're looking for. We'll search these every day.</p>

      <div>
        <label className="block text-sm font-bold mb-2">Search Queries</label>
        <TagInput value={prefs.queries || []} onChange={v => updateField('queries', v)} placeholder="e.g. Backend Engineer, Python Developer" />
      </div>

      <div>
        <label className="block text-sm font-bold mb-2">Locations</label>
        <TagInput value={prefs.locations || []} onChange={v => updateField('locations', v)} placeholder="e.g. Dublin, Remote, London" />
      </div>

      <div>
        <label className="block text-sm font-bold mb-2">Experience Level</label>
        <div className="flex gap-4">
          {[['entry_level', 'Entry Level'], ['mid_level', 'Mid Level'], ['senior', 'Senior']].map(([val, label]) => (
            <label key={val} className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={(prefs.experience_levels || []).includes(val)}
                onChange={() => toggleLevel(val)} className="w-4 h-4" />
              <span className="text-sm">{label}</span>
            </label>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Input label="Days Back" type="number" min={1} max={30}
          value={prefs.days_back || 7} onChange={e => updateField('days_back', parseInt(e.target.value) || 7)} />
        <Input label="Max Jobs per Run" type="number" min={1} max={100}
          value={prefs.max_jobs_per_run || 15} onChange={e => updateField('max_jobs_per_run', parseInt(e.target.value) || 15)} />
        <div>
          <label className="block text-sm font-bold mb-2">Min Match Score: {prefs.min_match_score || 60}</label>
          <input type="range" min={0} max={100} value={prefs.min_match_score || 60}
            onChange={e => updateField('min_match_score', parseInt(e.target.value))}
            className="w-full" />
        </div>
      </div>
    </div>
  )
}

// ─── Step 4: Done ───────────────────────────────────────────────
function StepDone() {
  return (
    <div className="text-center py-8 space-y-4">
      <div className="text-5xl">🎉</div>
      <h2 className="text-3xl font-heading font-bold">You're All Set!</h2>
      <p className="text-stone-600 text-lg max-w-md mx-auto">
        Your job search pipeline is ready. Jobs will start appearing on your Dashboard after the next run.
      </p>
    </div>
  )
}

// ─── Main Wizard ────────────────────────────────────────────────
const STEPS = ['Welcome', 'Resume', 'Profile', 'Preferences', 'Done']

export default function Onboarding() {
  const navigate = useNavigate()
  const { user } = useAuth()
  // After Complete Setup writes onboarding_completed_at, ProfileContext is stale;
  // refetch so AppLayout's gate sees the new value and doesn't bounce us back.
  const { refetch: refetchProfile } = useUserProfile()
  const [step, setStep] = useState(0)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  // Profile state (pre-filled from CV)
  const [profile, setProfile] = useState({
    full_name: '', email: user?.email || '', phone: '', location: '',
    github_url: '', linkedin_url: '', website: '',
    visa_status: '', work_authorizations: [],
    candidate_context: '', salary_expectation_notes: '', notice_period_text: '',
  })

  // Preferences state
  const [prefs, setPrefs] = useState({
    queries: [], locations: [], experience_levels: ['mid_level'],
    days_back: 7, max_jobs_per_run: 15, min_match_score: 60,
  })

  // Resume upload state
  const [resumeFile, setResumeFile] = useState(null)
  const [uploadStatus, setUploadStatus] = useState('idle') // idle | ready | uploading | done | error

  // Load existing profile if any
  useEffect(() => {
    apiGet('/api/profile').then(data => {
      if (data && data.full_name) {
        setProfile(prev => ({
          ...prev,
          full_name: data.full_name || prev.full_name,
          phone: data.phone || prev.phone,
          location: data.location || prev.location,
          github_url: data.github_url || prev.github_url,
          linkedin_url: data.linkedin_url || prev.linkedin_url,
          website: data.website || prev.website,
          visa_status: data.visa_status || prev.visa_status,
          work_authorizations: data.work_authorizations
            ? Object.entries(data.work_authorizations).map(([country, status]) => ({ country, status }))
            : prev.work_authorizations,
          candidate_context: data.candidate_context || prev.candidate_context,
          salary_expectation_notes: data.salary_expectation_notes || prev.salary_expectation_notes,
          notice_period_text: data.notice_period_text || prev.notice_period_text,
        }))
      }
    }).catch(() => {})

    apiGet('/api/search-config').then(data => {
      if (data) {
        setPrefs(prev => ({
          ...prev,
          queries: data.queries || prev.queries,
          locations: Array.isArray(data.locations) ? data.locations : prev.locations,
          experience_levels: data.experience_levels || prev.experience_levels,
          days_back: data.days_back || prev.days_back,
          max_jobs_per_run: data.max_jobs_per_run || prev.max_jobs_per_run,
          min_match_score: data.min_match_score || prev.min_match_score,
        }))
      }
    }).catch(() => {})
  }, [])

  // Called when CV parser extracts profile data
  function onExtracted(extracted) {
    setProfile(prev => ({
      ...prev,
      full_name: extracted.name || prev.full_name,
      phone: extracted.phone || prev.phone,
      location: extracted.location || prev.location,
      candidate_context: extracted.skills
        ? (typeof extracted.skills === 'string' ? extracted.skills : JSON.stringify(extracted.skills))
        : prev.candidate_context,
    }))
  }

  async function handleComplete() {
    setSaving(true)
    setError('')
    try {
      // Transform work_authorizations array → object
      const wa = {}
      for (const item of (profile.work_authorizations || [])) {
        if (item.country && item.status) wa[item.country] = item.status
      }

      // Email is auth-managed (Supabase) — backend ProfileUpdateRequest uses
      // extra="forbid" and rejects any unknown fields. Strip before sending.
      // We keep `email` in local state for display in StepProfile (line ~215).
      const { email: _email, ...profileForBackend } = profile

      await apiPut('/api/profile', {
        ...profileForBackend,
        work_authorizations: wa,
        complete_onboarding: true,
      })

      // Surface search-config save errors instead of silently dropping them.
      // We still let onboarding "complete" if only search-config fails (the
      // profile is the critical piece) but the user gets a warning toast.
      try {
        await apiPut('/api/search-config', prefs)
      } catch (cfgErr) {
        setError(`Profile saved, but search preferences failed to save: ${cfgErr.message}. Update them later in Settings.`)
        setSaving(false)
        return
      }

      // Refetch ProfileContext so AppLayout sees onboarding_completed_at + the
      // updated profile_complete flag. Without this, `navigate('/')` from the
      // Done screen bounces back to /onboarding (stale ProfileContext). Don't
      // let a refetch failure block the user — proceed regardless.
      try {
        await refetchProfile()
      } catch { /* swallow — Done screen still navigates */ }

      setSaving(false)
      next() // Advance to Done screen only after successful save
      return
    } catch (err) {
      setError(err.message || 'Failed to save. Please try again.')
      setSaving(false)
    }
  }

  function next() {
    if (step < STEPS.length - 1) setStep(step + 1)
  }
  function back() {
    if (step > 0) setStep(step - 1)
  }

  return (
    <div className="min-h-screen bg-cream flex items-center justify-center p-4">
      <div className="w-full max-w-2xl">
        <StepIndicator current={step} steps={STEPS} />

        <div className="border-2 border-black rounded-lg bg-white p-6 shadow-brutal">
          {step === 0 && <StepWelcome />}
          {step === 1 && (
            <StepResume resumeFile={resumeFile} setResumeFile={setResumeFile}
              uploadStatus={uploadStatus} setUploadStatus={setUploadStatus}
              onExtracted={onExtracted} />
          )}
          {step === 2 && <StepProfile profile={profile} setProfile={setProfile} />}
          {step === 3 && <StepPreferences prefs={prefs} setPrefs={setPrefs} />}
          {step === 4 && <StepDone />}

          {error && <p className="text-red-600 text-sm mt-4">{error}</p>}

          <div className="flex justify-between mt-8">
            <div>
              {step > 0 && step < 4 && (
                <Button variant="ghost" onClick={back}>← Back</Button>
              )}
            </div>
            <div className="flex gap-2">
              {step === 1 && uploadStatus !== 'uploading' && (
                <Button variant="ghost" onClick={next}>Skip</Button>
              )}
              {step < 3 && (
                <Button onClick={next}>Next →</Button>
              )}
              {step === 3 && (() => {
                // FU#8: at least one experience level must be selected so the
                // daily pipeline has a seniority filter to run against.
                const noLevels = !(prefs.experience_levels || []).length
                return (
                  <Button
                    onClick={handleComplete}
                    loading={saving}
                    disabled={noLevels}
                    title={noLevels ? 'Pick at least one Experience Level to continue' : undefined}
                  >
                    Complete Setup
                  </Button>
                )
              })()}
              {step === 4 && (
                <Button onClick={() => navigate('/', { replace: true })}>
                  Go to Dashboard →
                </Button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
