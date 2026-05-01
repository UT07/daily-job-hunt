import { useState, useRef, useCallback } from 'react';
import { apiCall, pollPipeline } from '../api';
import Button from '../components/ui/Button';
import Input, { Textarea, Select } from '../components/ui/Input';
import ScoreCard from '../components/ScoreCard';
import TailorCard from '../components/TailorCard';
import CoverLetterCard from '../components/CoverLetterCard';
import ContactsCard from '../components/ContactsCard';
import ErrorBanner from '../components/ErrorBanner';

// Step Functions pipeline progress steps
const PIPELINE_STEPS = [
  { key: 'STARTING',   label: 'Starting pipeline...' },
  { key: 'RUNNING',    label: 'Processing job...' },
  { key: 'SUCCEEDED',  label: 'Done!' },
];

// Legacy progress steps for non-pipeline actions (score, contacts)
const LEGACY_PROGRESS_STEPS = {
  score: [
    { key: 'scoring', label: 'Scoring job match...' },
    { key: 'done',    label: 'Done!' },
  ],
  contacts: [
    { key: 'finding', label: 'Finding contacts...' },
    { key: 'done',    label: 'Done!' },
  ],
};

// pollTask's default maxWaitMs is 240000 (4 min), which is fine for the
// fast `score` flow (~30s) but wrong for `contacts`: find_contacts on
// the backend can take 5-7 min in the worst case (up to 9 Apify Google
// searches × 60s each when Google rate-limits the scraper). A blanket
// bump to 10 min would mean `score` also hangs for 10 min if the backend
// dies, so we set the timeout per-key here instead. Defined at module
// scope so the object identity stays stable across renders (otherwise it
// would be a new dep on the runLegacy useCallback every render).
const LEGACY_MAX_WAIT_MS = { score: 120000, contacts: 600000 };

// Map raw task status strings to step keys (for legacy actions)
function statusToStepKey(rawStatus) {
  if (!rawStatus) return null;
  const s = rawStatus.toLowerCase();
  if (s.includes('scor')) return 'scoring';
  if (s.includes('contact') || s.includes('find')) return 'finding';
  if (s === 'done') return 'done';
  return null;
}

function ProgressIndicator({ steps, currentKey }) {
  const currentIdx = steps.findIndex((s) => s.key === currentKey);
  const doneKey = steps[steps.length - 1]?.key;

  return (
    <div className="flex items-center gap-2 flex-wrap">
      {steps.map((step, i) => {
        const isDone = i < currentIdx || currentKey === doneKey;
        const isActive = step.key === currentKey && currentKey !== doneKey;
        return (
          <div key={step.key} className="flex items-center gap-2">
            <div className={`flex items-center gap-1.5 px-2.5 py-1 border-2 font-mono text-[11px] font-bold
              ${isDone
                ? 'border-success bg-success-light text-success'
                : isActive
                  ? 'border-yellow-dark bg-yellow-light text-yellow-dark animate-pulse'
                  : 'border-stone-300 bg-stone-100 text-stone-400'
              }`}>
              {isDone ? '\u2713' : isActive ? '\u23F3' : '\u25CB'}
              <span>{step.label}</span>
            </div>
            {i < steps.length - 1 && (
              <span className="text-stone-300 font-mono text-xs">{'→'}</span>
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
  const [progressKey, setProgressKey] = useState(null);   // current step key for progress indicator
  const [progressSteps, setProgressSteps] = useState([]);  // which step list is active
  const [errors, setErrors] = useState([]);
  const abortRef = useRef(null);

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

  // Run the full pipeline via Step Functions (tailor + cover letter)
  const runPipeline = useCallback(async (action) => {
    if (!jd.trim()) return;
    setErrors([]);
    setActionLoading((prev) => ({ ...prev, [action]: true }));
    setProgressSteps(PIPELINE_STEPS);
    setProgressKey('STARTING');

    try {
      const payload = getPayload();

      // POST to Step Functions endpoint
      const res = await apiCall('/api/pipeline/run-single', payload);
      const { pollUrl } = res;
      if (!pollUrl) throw new Error('No pollUrl returned from pipeline');

      setProgressKey('RUNNING');

      // Poll until terminal state
      // Single-job pipeline takes 6-9 min in practice (tailor → compile →
      // cover letter → find contacts). 5 min was timing out before the SFN
      // finished — user saw "Tailor Resume doesn't work" while the backend
      // was actually succeeding silently. 15 min gives margin.
      const output = await pollPipeline(pollUrl, {
        intervalMs: 5000,
        maxWaitMs: 900000,
        onStatus: (data) => {
          if (data.status === 'SUCCEEDED') {
            setProgressKey('SUCCEEDED');
          }
          // stay on RUNNING for any non-terminal status
        },
      });

      setProgressKey('SUCCEEDED');

      // The pipeline output contains the full results.
      // Determine what to show based on the action and what's in the output.
      if (action === 'tailor') {
        addResult('tailor', output);
      } else if (action === 'cover-letter') {
        addResult('cover-letter', output);
      } else {
        // Generic: show whatever came back
        addResult(action, output);
      }
    } catch (err) {
      setErrors((prev) => [...prev, err.message]);
    } finally {
      setActionLoading((prev) => ({ ...prev, [action]: false }));
      setProgressKey(null);
      setProgressSteps([]);
    }
  }, [jd, jobTitle, company, location, applyUrl, resumeType]);

  const runLegacy = useCallback(async (endpoint, key) => {
    if (!jd.trim()) return;
    setErrors([]);
    setActionLoading((prev) => ({ ...prev, [key]: true }));
    const steps = LEGACY_PROGRESS_STEPS[key] || [];
    setProgressSteps(steps);
    setProgressKey(steps[0]?.key || null);

    try {
      const payload = getPayload();
      const data = await apiCall(endpoint, payload, {
        maxWaitMs: LEGACY_MAX_WAIT_MS[key],  // undefined → pollTask default
        onProgress: (status) => {
          const mapped = statusToStepKey(status);
          if (mapped) setProgressKey(mapped);
        },
      });
      setProgressKey('done');
      addResult(key, data);
    } catch (err) {
      setErrors((prev) => [...prev, err.message]);
    } finally {
      setActionLoading((prev) => ({ ...prev, [key]: false }));
      setProgressKey(null);
      setProgressSteps([]);
    }
  }, [jd, jobTitle, company, location, applyUrl, resumeType]);

  // Which action is currently in progress (if any)
  const activeKey = Object.keys(actionLoading).find((k) => actionLoading[k]);

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
            <span className="text-yellow-dark font-bold text-sm mt-0.5">{'\u26A0'}</span>
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
        {activeKey && progressSteps.length > 0 && (
          <div className="mb-4 p-3 border-2 border-black bg-stone-50">
            <ProgressIndicator steps={progressSteps} currentKey={progressKey} />
          </div>
        )}

        {/* Errors (single consolidated area) */}
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
            onClick={() => runLegacy('/api/score', 'score')}
            title="Score this JD against your base resume — also saves the job to your dashboard."
          >
            Save &amp; Score
          </Button>
          <Button
            variant="accent"
            loading={actionLoading.tailor}
            disabled={!jd.trim() || !!activeKey}
            onClick={() => runPipeline('tailor')}
          >
            Tailor Resume
          </Button>
          <Button
            variant="secondary"
            loading={actionLoading['cover-letter']}
            disabled={!jd.trim() || !!activeKey}
            onClick={() => runPipeline('cover-letter')}
          >
            Cover Letter
          </Button>
          <Button
            variant="secondary"
            loading={actionLoading.contacts}
            disabled={!jd.trim() || !!activeKey}
            onClick={() => runLegacy('/api/contacts', 'contacts')}
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
