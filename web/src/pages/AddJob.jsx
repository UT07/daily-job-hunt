import { useState } from 'react';
import { apiCall } from '../api';
import Button from '../components/ui/Button';
import Input, { Textarea, Select } from '../components/ui/Input';
import ScoreCard from '../components/ScoreCard';
import TailorCard from '../components/TailorCard';
import CoverLetterCard from '../components/CoverLetterCard';
import ContactsCard from '../components/ContactsCard';
import ErrorBanner from '../components/ErrorBanner';

const PROGRESS_STEPS = {
  score: [
    { key: 'scoring',  label: 'Scoring job match...' },
    { key: 'done',     label: 'Done!' },
  ],
  tailor: [
    { key: 'scoring',       label: 'Scoring job match...' },
    { key: 'tailoring',     label: 'Tailoring resume...' },
    { key: 'done',          label: 'Done!' },
  ],
  'cover-letter': [
    { key: 'scoring',       label: 'Scoring job match...' },
    { key: 'cover_letter',  label: 'Generating cover letter...' },
    { key: 'done',          label: 'Done!' },
  ],
  contacts: [
    { key: 'finding',   label: 'Finding contacts...' },
    { key: 'done',      label: 'Done!' },
  ],
};

// Map raw task status strings to step keys
function statusToStepKey(rawStatus) {
  if (!rawStatus) return null;
  const s = rawStatus.toLowerCase();
  if (s.includes('scor')) return 'scoring';
  if (s.includes('tailor')) return 'tailoring';
  if (s.includes('cover')) return 'cover_letter';
  if (s.includes('contact') || s.includes('find')) return 'finding';
  if (s === 'done') return 'done';
  return null;
}

function ProgressIndicator({ steps, currentStatus }) {
  const currentKey = statusToStepKey(currentStatus) || steps[0]?.key;
  const currentIdx = steps.findIndex((s) => s.key === currentKey);

  return (
    <div className="flex items-center gap-2 flex-wrap">
      {steps.map((step, i) => {
        const isDone = i < currentIdx || currentKey === 'done';
        const isActive = step.key === currentKey && currentKey !== 'done';
        return (
          <div key={step.key} className="flex items-center gap-2">
            <div className={`flex items-center gap-1.5 px-2.5 py-1 border-2 font-mono text-[11px] font-bold
              ${isDone
                ? 'border-success bg-success-light text-success'
                : isActive
                  ? 'border-yellow-dark bg-yellow-light text-yellow-dark animate-pulse'
                  : 'border-stone-300 bg-stone-100 text-stone-400'
              }`}>
              {isDone ? '✓' : isActive ? '⏳' : '○'}
              <span>{step.label}</span>
            </div>
            {i < steps.length - 1 && (
              <span className="text-stone-300 font-mono text-xs">→</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function AddJob() {
  const [jd, setJd] = useState('');
  const [jobTitle, setJobTitle] = useState('Software Engineer');
  const [company, setCompany] = useState('');
  const [location, setLocation] = useState('');
  const [applyUrl, setApplyUrl] = useState('');
  const [resumeType, setResumeType] = useState('sre_devops');
  const [results, setResults] = useState([]);
  const [actionLoading, setActionLoading] = useState({});
  const [actionProgress, setActionProgress] = useState({});
  const [errors, setErrors] = useState([]);

  const jdTooShort = jd.trim().length > 0 && jd.trim().length < 100;

  function getPayload() {
    return {
      job_description: jd,
      job_title: jobTitle,
      company,
      location,
      apply_url: applyUrl,
      resume_type: resumeType,
    };
  }

  function addResult(type, data) {
    setResults((prev) => [{ type, data, company }, ...prev]);
  }

  async function run(endpoint, key) {
    if (!jd.trim()) return;
    // Clear previous errors for this action
    setErrors([]);
    setActionLoading((prev) => ({ ...prev, [key]: true }));
    setActionProgress((prev) => ({ ...prev, [key]: null }));
    try {
      const payload = getPayload();
      const steps = PROGRESS_STEPS[key] || [];
      const data = await apiCall(endpoint, payload, {
        onProgress: (status) => {
          setActionProgress((prev) => ({ ...prev, [key]: status }));
        },
      });
      addResult(key, data);
    } catch (err) {
      setErrors((prev) => [...prev, err.message]);
    } finally {
      setActionLoading((prev) => ({ ...prev, [key]: false }));
      setActionProgress((prev) => ({ ...prev, [key]: null }));
    }
  }

  // Which action is currently in progress (if any)
  const activeKey = Object.keys(actionLoading).find((k) => actionLoading[k]);
  const activeSteps = activeKey ? PROGRESS_STEPS[activeKey] || [] : [];
  const activeProgress = activeKey ? actionProgress[activeKey] : null;

  return (
    <div>
      {/* Page header */}
      <div className="mb-6">
        <h1 className="text-2xl font-heading font-bold text-black tracking-tight">Add Job</h1>
        <p className="text-sm text-stone-500 mt-1">
          Paste a job description to score, tailor a resume, generate a cover letter, or find contacts.
        </p>
      </div>

      {/* Form card */}
      <div className="bg-white border-2 border-black shadow-brutal p-6 mb-6">
        {/* Job description */}
        <div className="mb-1">
          <Textarea
            label="Job Description"
            id="jd"
            rows={8}
            placeholder="Paste the full job description here..."
            value={jd}
            onChange={(e) => setJd(e.target.value)}
          />
        </div>
        {/* JD length warning */}
        {jdTooShort && (
          <div className="mb-4 mt-2 flex items-start gap-2 border-2 border-yellow-dark bg-yellow-light px-3 py-2">
            <span className="text-yellow-dark font-bold text-sm mt-0.5">⚠</span>
            <p className="text-xs font-bold text-yellow-dark leading-relaxed">
              Job description seems too short. AI matching works best with a detailed JD
              (responsibilities, requirements, tech stack).
            </p>
          </div>
        )}
        {!jdTooShort && <div className="mb-4" />}

        {/* Core metadata: title, company, resume type */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
          <Input
            label="Job Title"
            id="job-title"
            placeholder="e.g. Senior Engineer"
            value={jobTitle}
            onChange={(e) => setJobTitle(e.target.value)}
          />
          <Input
            label="Company"
            id="company"
            placeholder="e.g. Stripe"
            value={company}
            onChange={(e) => setCompany(e.target.value)}
          />
          <Select
            label="Resume Type"
            id="resume-type"
            value={resumeType}
            onChange={(e) => setResumeType(e.target.value)}
          >
            <option value="sre_devops">SRE / DevOps Engineer</option>
            <option value="fullstack">Full-Stack Software Engineer</option>
          </Select>
        </div>

        {/* Optional fields: location, apply URL */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
          <Input
            label="Location (optional)"
            id="location"
            placeholder="e.g. Dublin, Ireland or Remote"
            value={location}
            onChange={(e) => setLocation(e.target.value)}
          />
          <Input
            label="Apply URL (optional)"
            id="apply-url"
            type="url"
            placeholder="https://..."
            value={applyUrl}
            onChange={(e) => setApplyUrl(e.target.value)}
          />
        </div>

        {/* Progress indicator (shown while any action is running) */}
        {activeKey && activeSteps.length > 0 && (
          <div className="mb-4 p-3 border-2 border-black bg-stone-50">
            <ProgressIndicator steps={activeSteps} currentStatus={activeProgress} />
          </div>
        )}

        {/* Errors (single consolidated area, replaces stacking cards) */}
        {errors.length > 0 && (
          <div className="mb-4 space-y-2">
            {errors.map((msg, i) => (
              <ErrorBanner key={i} message={msg} />
            ))}
          </div>
        )}

        {/* Action buttons */}
        <div className="flex flex-wrap gap-3">
          <Button
            variant="secondary"
            loading={actionLoading.score}
            disabled={!jd.trim() || !!activeKey}
            onClick={() => run('/api/score', 'score')}
          >
            Score Resume
          </Button>
          <Button
            variant="accent"
            loading={actionLoading.tailor}
            disabled={!jd.trim() || !!activeKey}
            onClick={() => run('/api/tailor', 'tailor')}
          >
            Tailor Resume
          </Button>
          <Button
            variant="secondary"
            loading={actionLoading['cover-letter']}
            disabled={!jd.trim() || !!activeKey}
            onClick={() => run('/api/cover-letter', 'cover-letter')}
          >
            Cover Letter
          </Button>
          <Button
            variant="secondary"
            loading={actionLoading.contacts}
            disabled={!jd.trim() || !!activeKey}
            onClick={() => run('/api/contacts', 'contacts')}
          >
            Find Contacts
          </Button>
        </div>
      </div>

      {/* Results */}
      {results.length > 0 && (
        <div className="space-y-4">
          {results.map((result, i) => {
            if (result.type === 'score') {
              return <ScoreCard key={i} data={result.data} company={result.company} />;
            }
            if (result.type === 'tailor') {
              return <TailorCard key={i} data={result.data} company={result.company} />;
            }
            if (result.type === 'cover-letter') {
              return <CoverLetterCard key={i} data={result.data} company={result.company} />;
            }
            if (result.type === 'contacts') {
              return <ContactsCard key={i} data={result.data} company={result.company} />;
            }
            return null;
          })}
        </div>
      )}
    </div>
  );
}
