import { useState, useRef, useEffect, useCallback } from 'react';
import { apiCall, pollPipeline } from '../api';
import Button from '../components/ui/Button';
import Input, { Textarea, Select } from '../components/ui/Input';
import ScoreCard from '../components/ScoreCard';
import TailorCard from '../components/TailorCard';
import CoverLetterCard from '../components/CoverLetterCard';
import ContactsCard from '../components/ContactsCard';
import ErrorBanner from '../components/ErrorBanner';
import { extractJobFields } from '../lib/jdExtract';

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

// ---- Draft persistence ----
//
// AddJob is the one page where navigating away destroys real user work: a
// pasted JD runs to several KB and is not recoverable once the page unmounts.
// Dashboard solves the same "state didn't survive navigation" problem with
// readFilterFromParams + useSearchParams; this is deliberately that same
// shape — a DEFAULTS map, a typed per-field reader used in lazy useState
// initialisers, and one effect that writes back only the non-default fields.
//
// The store is sessionStorage rather than the query string, for three
// reasons specific to this form: the JD alone routinely exceeds the ~2KB that
// proxies and older browsers will carry in a URL; a full job description in
// the address bar leaks into history, bookmarks and Referer headers; and a
// per-tab lifetime matches how the draft is actually used (type here, go
// check the dashboard, come back and finish) without resurrecting a stale JD
// in a brand-new tab days later.
const DRAFT_STORAGE_KEY = 'naukribaba_addjob_draft';

const DRAFT_DEFAULTS = {
  jd: '',
  job_title: 'Software Engineer',
  company: '',
  location: '',
  apply_url: '',
  resume_type: 'sre_devops',
};

function readDraft() {
  try {
    const raw = sessionStorage.getItem(DRAFT_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    // Disabled storage, a private-mode quota error, or a corrupt blob left by
    // an older draft shape. An empty form is the right fallback here; losing
    // a draft is survivable, crashing the page on mount is not.
    return {};
  }
}

function readDraftField(draft, key, fallback) {
  const raw = draft[key];
  return typeof raw === 'string' ? raw : fallback;
}

function writeDraft(values) {
  try {
    const next = {};
    for (const [key, value] of Object.entries(values)) {
      if (value === DRAFT_DEFAULTS[key] || value === '') continue;
      next[key] = value;
    }
    // A pristine (or cleared-out) form leaves nothing behind, so the next
    // visit starts clean instead of restoring an empty draft over the
    // defaults.
    if (Object.keys(next).length === 0) {
      sessionStorage.removeItem(DRAFT_STORAGE_KEY);
    } else {
      sessionStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(next));
    }
  } catch {
    // Persistence is a convenience; never let it break typing.
  }
}

// Map raw task status strings to step keys (for legacy actions)
function statusToStepKey(rawStatus) {
  if (!rawStatus) return null;
  const s = rawStatus.toLowerCase();
  if (s.includes('scor')) return 'scoring';
  if (s.includes('contact') || s.includes('find')) return 'finding';
  if (s === 'done') return 'done';
  return null;
}

// How a detected value was arrived at, in the user's terms. "from the apply
// link" is a fact read out of a URL -- 26 of 26 were right when measured
// against real pastes. "from the description" is a guess at prose and is worth
// a second look, which is why the two are not worded the same.
const DETECTED_LABEL = {
  'ats-url': 'from the apply link',
  domain: 'from the apply link',
  body: 'from the description',
  label: 'from the description',
};

function DetectedHint({ source }) {
  if (!source) return null;
  return (
    <p className="mt-1 flex items-center gap-1 font-mono text-[10px] font-bold uppercase tracking-wide text-stone-500">
      <span aria-hidden="true">{'\u2728'}</span>
      <span>auto-filled {DETECTED_LABEL[source] || 'from your paste'} \u2014 edit if wrong</span>
    </p>
  );
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
  // One parse at mount, shared by the six lazy initialisers below — the role
  // `searchParams` plays for Dashboard's readFilterFromParams calls.
  const [draft] = useState(readDraft);

  const [jd, setJd] = useState(() => readDraftField(draft, 'jd', DRAFT_DEFAULTS.jd));
  const [jobTitle, setJobTitle] = useState(() => readDraftField(draft, 'job_title', DRAFT_DEFAULTS.job_title));
  const [company, setCompany] = useState(() => readDraftField(draft, 'company', DRAFT_DEFAULTS.company));
  const [location, setLocation] = useState(() => readDraftField(draft, 'location', DRAFT_DEFAULTS.location));
  const [applyUrl, setApplyUrl] = useState(() => readDraftField(draft, 'apply_url', DRAFT_DEFAULTS.apply_url));
  const [resumeType, setResumeType] = useState(() => readDraftField(draft, 'resume_type', DRAFT_DEFAULTS.resume_type));
  // What the paste told us, and which fields the user has taken over.
  //
  // Auto-fill only ever writes into a field that is still empty (or still the
  // default) AND that the user has not edited. Overwriting typed input would
  // be worse than filling nothing: nobody re-reads a box they already filled,
  // so a clobbered company would ship silently.
  const [detected, setDetected] = useState({});
  const touched = useRef({});
  const markTouched = (key) => { touched.current[key] = true; };
  const forget = (key) => setDetected((d) => {
    if (!(key in d)) return d;
    const next = { ...d };
    delete next[key];
    return next;
  });

  const [draftRestored, setDraftRestored] = useState(
    () => Object.keys(draft).length > 0);

  const [results, setResults] = useState([]);
  const [actionLoading, setActionLoading] = useState({});
  const [progressKey, setProgressKey] = useState(null);   // current step key for progress indicator
  const [progressSteps, setProgressSteps] = useState([]);  // which step list is active
  const [errors, setErrors] = useState([]);
  const abortRef = useRef(null);

  const jdTooShort = jd.trim().length > 0 && jd.trim().length < 100;

  // Mirrors Dashboard's URL-sync effect: runs on every field change and
  // persists only what differs from the defaults. `results` is intentionally
  // not persisted — the payloads are large and each one is re-derivable by
  // re-running the action against the restored JD.
  useEffect(() => {
    writeDraft({
      jd,
      job_title: jobTitle,
      company,
      location,
      apply_url: applyUrl,
      resume_type: resumeType,
    });
  }, [jd, jobTitle, company, location, applyUrl, resumeType]);

  // Read what we can off the paste. Depends on the JD and the apply URL only:
  // those are the two things the user supplies; the rest is derived from them.
  useEffect(() => {
    if (!jd.trim() && !applyUrl.trim()) return;
    const { fields, sources } = extractJobFields(jd, applyUrl);
    const filled = {};

    if (fields.company && !company.trim() && !touched.current.company) {
      setCompany(fields.company);
      filled.company = sources.company;
    }
    // Title's "empty" is the DEFAULT, not ''. The form ships with
    // "Software Engineer" pre-filled, so treating only '' as empty would mean
    // a detected title could never land.
    if (fields.title
        && (!jobTitle.trim() || jobTitle === DRAFT_DEFAULTS.job_title)
        && !touched.current.job_title) {
      setJobTitle(fields.title);
      filled.job_title = sources.title;
    }
    if (fields.location && !location.trim() && !touched.current.location) {
      setLocation(fields.location);
      filled.location = sources.location;
    }
    if (Object.keys(filled).length) setDetected((prev) => ({ ...prev, ...filled }));
    // company/jobTitle/location are read to decide "is this field empty", but
    // listing them would re-run this on our own setState and fight the user's
    // typing. jd + applyUrl are the real inputs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jd, applyUrl]);


  // Reset to a pristine form, store included, so navigating away and back
  // does not restore what the user just dismissed.
  function clearDraft() {
    setJd(DRAFT_DEFAULTS.jd);
    setJobTitle(DRAFT_DEFAULTS.job_title);
    setCompany(DRAFT_DEFAULTS.company);
    setLocation(DRAFT_DEFAULTS.location);
    setApplyUrl(DRAFT_DEFAULTS.apply_url);
    setResumeType(DRAFT_DEFAULTS.resume_type);
    setResults([]);
    setErrors([]);
    setDetected({});
    touched.current = {};
    setDraftRestored(false);
    try {
      // Belt and braces, and known to be so: the write-back effect also
      // removes the key once every field is back to its default, so a
      // mutation deleting this line survives the suite. Kept because it clears
      // the store in the same tick as the click rather than on the next
      // commit, and noted here so the next reader does not take the surviving
      // mutant for dead code.
      sessionStorage.removeItem(DRAFT_STORAGE_KEY);
    } catch { /* storage disabled; the state reset above is what matters */ }
  }

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

  // `extra` is merged over the form payload. Used for { force: true }, which
  // tells /api/score to re-run the model instead of returning the score it
  // already has on record.
  const runLegacy = useCallback(async (endpoint, key, extra) => {
    if (!jd.trim()) return;
    setErrors([]);
    setActionLoading((prev) => ({ ...prev, [key]: true }));
    const steps = LEGACY_PROGRESS_STEPS[key] || [];
    setProgressSteps(steps);
    setProgressKey(steps[0]?.key || null);

    try {
      const payload = { ...getPayload(), ...(extra || {}) };
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
        {/* A restored draft has to announce itself.
            Reported 2026-10-08: the user opened Add Job to enter a new job and
            the previous night's Grinds360 description was still in the box.
            sessionStorage lives as long as the TAB, so a tab left open
            overnight restores in silence -- they typed over it and could not
            tell what had been kept. The draft is still worth keeping, because
            a pasted JD is unrecoverable work. What was missing is this line. */}
        {draftRestored && (
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2 border-2 border-stone-400 bg-stone-100 px-3 py-2">
            <p className="font-mono text-[11px] font-bold text-stone-600">
              {'\u21BB'} Restored the draft you left in this tab.
            </p>
            <button
              type="button"
              onClick={clearDraft}
              className="border-2 border-black bg-white px-2 py-0.5 font-mono text-[11px] font-bold uppercase hover:bg-stone-200"
            >
              Start fresh
            </button>
          </div>
        )}
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
          {/* field + hint share one grid cell; as siblings of the grid they
              would each become a column and push Resume Type onto a new row */}
          <div>
            <Input
              label="Job Title"
              id="job-title"
              placeholder="e.g. Senior Engineer"
              value={jobTitle}
              onChange={(e) => { markTouched('job_title'); setJobTitle(e.target.value); forget('job_title'); }}
            />
            <DetectedHint source={detected.job_title} />
          </div>
          {/* field + hint share one grid cell; as siblings of the grid they
              would each become a column and push Resume Type onto a new row */}
          <div>
            <Input
              label="Company"
              id="company"
              placeholder="e.g. Stripe"
              value={company}
              onChange={(e) => { markTouched('company'); setCompany(e.target.value); forget('company'); }}
            />
            <DetectedHint source={detected.company} />
          </div>
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
          <div>
            <Input
              label="Location (optional)"
              id="location"
              placeholder="e.g. Dublin, Ireland or Remote"
              value={location}
              onChange={(e) => { markTouched('location'); setLocation(e.target.value); forget('location'); }}
            />
            <DetectedHint source={detected.location} />
          </div>
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
              return (
                <ScoreCard
                  key={i}
                  data={result.data}
                  company={result.company}
                  onRescore={() => runLegacy('/api/score', 'score', { force: true })}
                  rescoring={!!actionLoading.score}
                />
              );
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
