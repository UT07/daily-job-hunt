import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { apiGet, apiPatch, apiCall } from '../api';

function decodeHtml(text) {
  if (!text) return '';
  const doc = new DOMParser().parseFromString(text, 'text/html');
  return doc.body.textContent || '';
}
import { ArrowLeft, Pencil, Save, X } from 'lucide-react';
import Tabs from '../components/ui/Tabs';
import Button from '../components/ui/Button';
import Input from '../components/ui/Input';
import { ScoreBadge } from '../components/ui/Badge';
import Badge from '../components/ui/Badge';

// ---- Contacts tab ----
function ContactItem({ contact }) {
  const [copied, setCopied] = useState(false);

  const copy = () => {
    navigator.clipboard.writeText(contact.message || '').then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  const linkUrl = contact.profile_url || contact.google_url || contact.search_url;
  const linkLabel = contact.profile_url
    ? 'View Profile'
    : contact.google_url
      ? 'Find on Google'
      : 'Search LinkedIn';

  return (
    <div className="border-2 border-black shadow-brutal-sm bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          {contact.name && (
            <p className="text-sm font-bold text-black">{contact.name}</p>
          )}
          <p className={`text-sm ${contact.name ? 'text-stone-600' : 'font-bold text-black'}`}>
            {contact.role}
          </p>
          {contact.why && (
            <p className="text-xs text-stone-400 mt-0.5 font-mono">{contact.why}</p>
          )}
        </div>
        {linkUrl && (
          <a
            href={linkUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="shrink-0 text-xs px-2 py-1 border-2 border-black font-bold
              hover:bg-yellow-light transition-colors"
          >
            {linkLabel}
          </a>
        )}
      </div>
      {contact.message && (
        <div className="mt-3 bg-stone-50 border-2 border-stone-200 p-3 relative">
          <p className="pr-16 font-mono text-xs leading-relaxed text-stone-600">
            {contact.message}
          </p>
          <button
            onClick={copy}
            className="absolute top-2 right-2 text-xs px-2 py-1 border-2 border-black font-bold
              bg-white hover:bg-yellow-light transition-colors"
          >
            {copied ? 'Copied!' : 'Copy'}
          </button>
        </div>
      )}
    </div>
  );
}

function ContactsTab({ job }) {
  const [findingContacts, setFindingContacts] = useState(false);

  let contacts = [];
  if (job.linkedin_contacts) {
    try {
      const parsed = typeof job.linkedin_contacts === 'string'
        ? JSON.parse(job.linkedin_contacts)
        : job.linkedin_contacts;
      contacts = Array.isArray(parsed) ? parsed : parsed.contacts || [];
    } catch {
      contacts = [];
    }
  }

  async function handleFindContacts() {
    setFindingContacts(true);
    try {
      await apiCall(`/api/dashboard/jobs/${job.job_id}/find-contacts`, {});
    } catch (err) {
      console.error('Find contacts failed:', err);
    } finally {
      setFindingContacts(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between mb-3">
        <p className="text-xs font-bold text-stone-400 uppercase tracking-wider">
          {contacts.length} Contact{contacts.length !== 1 ? 's' : ''} · {job.company}
        </p>
        <Button
          variant="secondary"
          size="sm"
          loading={findingContacts}
          disabled={findingContacts}
          onClick={handleFindContacts}
        >
          {findingContacts ? 'Finding...' : contacts.length ? 'Find More' : 'Find Contacts'}
        </Button>
      </div>
      {contacts.length > 0 ? (
        contacts.map((c, i) => (
          <ContactItem key={i} contact={c} />
        ))
      ) : (
        <div className="text-center py-10">
          <p className="text-stone-400 text-sm">No contacts found yet. Click "Find Contacts" above.</p>
        </div>
      )}
    </div>
  );
}

// ---- Application Timeline ----

const VALID_STATUSES = ['New', 'Applied', 'Phone Screen', 'Interview', 'Offer', 'Rejected', 'Withdrawn', 'Accepted'];

function timeAgo(isoString) {
  const seconds = Math.floor((Date.now() - new Date(isoString).getTime()) / 1000);
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} day${days !== 1 ? 's' : ''} ago`;
  const months = Math.floor(days / 30);
  return `${months} month${months !== 1 ? 's' : ''} ago`;
}

function ApplicationTimeline({ jobId, events, onEventAdded }) {
  const [showForm, setShowForm] = useState(false);
  const [selectedStatus, setSelectedStatus] = useState('');
  const [notes, setNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!selectedStatus) return;
    setSubmitting(true);
    setError(null);
    try {
      const event = await apiCall(`/api/dashboard/jobs/${jobId}/timeline`, {
        status: selectedStatus,
        notes: notes.trim() || null,
      });
      onEventAdded(event, selectedStatus);
      setShowForm(false);
      setSelectedStatus('');
      setNotes('');
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mb-6">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-bold text-stone-400 uppercase tracking-wider">Application Timeline</h3>
        {!showForm && (
          <button
            onClick={() => setShowForm(true)}
            className="inline-flex items-center gap-1.5 text-xs font-bold text-stone-500 border-2 border-stone-300 px-2.5 py-1
              hover:border-black hover:text-black transition-colors cursor-pointer"
          >
            Update Status
          </button>
        )}
      </div>

      {showForm && (
        <form
          onSubmit={handleSubmit}
          className="mb-4 border-2 border-yellow bg-yellow-light p-4"
        >
          <div className="flex flex-col sm:flex-row gap-3 mb-3">
            <div className="flex-1">
              <label className="block text-xs font-bold text-black mb-1 uppercase tracking-wider">New Status</label>
              <select
                value={selectedStatus}
                onChange={(e) => setSelectedStatus(e.target.value)}
                required
                className="w-full border-2 border-black bg-white text-sm font-bold px-3 py-2 appearance-none focus:outline-none focus:border-yellow-dark"
              >
                <option value="">Select status...</option>
                {VALID_STATUSES.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="mb-3">
            <label className="block text-xs font-bold text-black mb-1 uppercase tracking-wider">Notes (optional)</label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={2}
              placeholder="e.g. Submitted via company portal"
              className="w-full border-2 border-black bg-white text-sm px-3 py-2 resize-none focus:outline-none focus:border-yellow-dark font-mono"
            />
          </div>
          {error && (
            <p className="text-xs text-error mb-2 font-mono">{error}</p>
          )}
          <div className="flex items-center gap-2">
            <button
              type="submit"
              disabled={submitting || !selectedStatus}
              className="text-xs font-bold text-cream bg-black border-2 border-black px-3 py-1.5
                hover:bg-stone-700 transition-colors cursor-pointer disabled:opacity-50"
            >
              {submitting ? 'Saving...' : 'Save'}
            </button>
            <button
              type="button"
              onClick={() => { setShowForm(false); setError(null); }}
              className="text-xs font-bold text-stone-500 border-2 border-stone-300 px-3 py-1.5
                hover:border-black hover:text-black transition-colors cursor-pointer"
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {events.length === 0 ? (
        <p className="text-xs text-stone-400 font-mono py-2">No status updates yet. Click "Update Status" to record your first action.</p>
      ) : (
        <div className="relative">
          {/* Vertical line */}
          <div className="absolute left-[7px] top-2 bottom-2 w-0.5 bg-stone-300" />
          <div className="space-y-3 pl-6">
            {events.map((ev) => (
              <div key={ev.id} className="relative">
                {/* Dot on the timeline */}
                <div className="absolute -left-6 top-1.5 w-3.5 h-3.5 border-2 border-black bg-white" />
                <div className="border-2 border-stone-200 bg-white p-3">
                  <div className="flex items-center gap-2 mb-1">
                    <Badge status={ev.status} />
                    <span className="text-[10px] font-mono text-stone-400">{timeAgo(ev.created_at)}</span>
                  </div>
                  {ev.notes && (
                    <p className="text-xs text-stone-600 font-mono leading-relaxed">{ev.notes}</p>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

const JOB_TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'research', label: 'Research' },
  { id: 'resume', label: 'Resume' },
  { id: 'cover-letter', label: 'Cover Letter' },
  { id: 'contacts', label: 'Contacts' },
  { id: 'prep', label: 'Interview Prep' },
];

export default function JobWorkspace() {
  const { jobId } = useParams();
  const navigate = useNavigate();
  const [job, setJob] = useState(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('overview');

  // Inline editing state
  const [editing, setEditing] = useState(false);
  const [editFields, setEditFields] = useState({});
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState(null);
  const [regenLoading, setRegenLoading] = useState(null); // 'resume' | 'cover' | null

  // Timeline state
  const [timeline, setTimeline] = useState([]);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [currentStatus, setCurrentStatus] = useState(null); // tracks latest status from timeline

  function startEditing() {
    setEditFields({
      title: job.title || '',
      company: job.company || '',
      location: job.location || '',
      apply_url: job.apply_url || '',
    });
    setEditing(true);
    setSaveStatus(null);
  }

  function cancelEditing() {
    setEditing(false);
    setEditFields({});
    setSaveStatus(null);
  }

  async function handleSave() {
    setSaving(true);
    setSaveStatus(null);
    try {
      await apiPatch(`/api/dashboard/jobs/${job.job_id}`, editFields);
      setJob((prev) => ({ ...prev, ...editFields }));
      setEditing(false);
      setSaveStatus({ type: 'success', message: 'Job updated.' });
    } catch (e) {
      setSaveStatus({ type: 'error', message: `Save failed: ${e.message}` });
    } finally {
      setSaving(false);
    }
  }

  function updateEditField(field, value) {
    setEditFields((prev) => ({ ...prev, [field]: value }));
  }

  async function handleRegen(type) {
    setRegenLoading(type);
    try {
      const data = await apiCall(`/api/pipeline/re-tailor/${job.job_id}`, {});
      const execName = data.pollUrl?.split('/').pop();
      if (!execName) throw new Error('No execution ID returned');

      // Poll until done
      const poll = setInterval(async () => {
        try {
          const result = await apiGet(`/api/pipeline/status/${execName}`);
          if (result.status !== 'RUNNING') {
            clearInterval(poll);
            setRegenLoading(null);
            // Refresh job data to get new PDF URLs
            const updated = await apiGet(`/api/dashboard/jobs/${job.job_id}`);
            if (updated) setJob(updated);
          }
        } catch (err) {
          console.error('Regen poll error:', err);
        }
      }, 5000);
    } catch (err) {
      console.error('Regen failed:', err);
      setRegenLoading(null);
    }
  }

  useEffect(() => {
    async function load() {
      try {
        const data = await apiGet(`/api/dashboard/jobs/${jobId}`);
        setJob(data || null);
        if (data) setCurrentStatus(data.application_status || 'New');
      } catch (err) {
        console.error('Failed to load job:', err);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [jobId]);

  useEffect(() => {
    if (!jobId) return;
    setTimelineLoading(true);
    apiGet(`/api/dashboard/jobs/${jobId}/timeline`)
      .then((data) => setTimeline(Array.isArray(data) ? data : []))
      .catch((err) => console.error('Failed to load timeline:', err))
      .finally(() => setTimelineLoading(false));
  }, [jobId]);

  function handleTimelineEventAdded(event, newStatus) {
    setTimeline((prev) => [event, ...prev]);
    setCurrentStatus(newStatus);
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <span className="spinner" />
      </div>
    );
  }

  if (!job) {
    return (
      <div className="border-2 border-black bg-white p-8 text-center">
        <p className="text-stone-500 font-heading">Job not found.</p>
        <Button variant="ghost" onClick={() => navigate('/')} className="mt-4">
          Back to Dashboard
        </Button>
      </div>
    );
  }

  return (
    <div>
      {/* Back button + header */}
      <div className="flex items-center gap-4 mb-4">
        <button
          onClick={() => navigate('/')}
          className="text-stone-400 hover:text-black transition-colors cursor-pointer"
        >
          <ArrowLeft size={20} />
        </button>
        <div className="flex-1">
          <h1 className="text-xl font-heading font-bold text-black tracking-tight">
            {decodeHtml(job.title)}
          </h1>
          <p className="text-sm text-stone-500">
            {decodeHtml(job.company)} {job.location && `· ${decodeHtml(job.location)}`}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <ScoreBadge score={job.match_score} className="text-2xl" />
          <Badge status={currentStatus || job.application_status || 'New'} />
          {job.apply_url && job.apply_url !== 'Apply' && (
            <a href={job.apply_url} target="_blank" rel="noopener noreferrer">
              <Button variant="accent" size="sm">Apply</Button>
            </a>
          )}
        </div>
      </div>

      {/* Tabs */}
      <Tabs tabs={JOB_TABS} activeTab={activeTab} onTabChange={setActiveTab} />

      {/* Tab content */}
      <div className="border-2 border-t-0 border-black bg-white p-6 min-h-[300px]">
        {activeTab === 'overview' && (
          <div>
            {/* Application Timeline */}
            {timelineLoading ? (
              <div className="mb-6 flex items-center gap-2">
                <span className="spinner" />
                <span className="text-xs text-stone-400 font-mono">Loading timeline...</span>
              </div>
            ) : (
              <ApplicationTimeline
                jobId={jobId}
                events={timeline}
                onEventAdded={handleTimelineEventAdded}
              />
            )}

            {/* Inline editable fields */}
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-xs font-bold text-stone-400 uppercase tracking-wider">Job Details</h3>
              {!editing ? (
                <button
                  onClick={startEditing}
                  className="inline-flex items-center gap-1.5 text-xs font-bold text-stone-500 border-2 border-stone-300 px-2.5 py-1
                    hover:border-black hover:text-black transition-colors cursor-pointer"
                >
                  <Pencil size={12} />
                  Edit
                </button>
              ) : (
                <div className="flex items-center gap-2">
                  <button
                    onClick={handleSave}
                    disabled={saving}
                    className="inline-flex items-center gap-1.5 text-xs font-bold text-cream bg-black border-2 border-black px-2.5 py-1
                      hover:bg-stone-700 transition-colors cursor-pointer disabled:opacity-50"
                  >
                    <Save size={12} />
                    {saving ? 'Saving...' : 'Save'}
                  </button>
                  <button
                    onClick={cancelEditing}
                    className="inline-flex items-center gap-1.5 text-xs font-bold text-stone-500 border-2 border-stone-300 px-2.5 py-1
                      hover:border-black hover:text-black transition-colors cursor-pointer"
                  >
                    <X size={12} />
                    Cancel
                  </button>
                </div>
              )}
            </div>

            {saveStatus && (
              <div className={`mb-4 p-2.5 text-sm border-2 ${
                saveStatus.type === 'success' ? 'bg-success-light border-success text-success' : 'bg-error-light border-error text-error'
              }`}>
                {saveStatus.message}
              </div>
            )}

            {editing ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-6 border-2 border-yellow bg-yellow-light p-4">
                <div>
                  <label className="block text-sm font-bold text-black mb-1">Title</label>
                  <Input
                    type="text"
                    value={editFields.title}
                    onChange={(e) => updateEditField('title', e.target.value)}
                  />
                </div>
                <div>
                  <label className="block text-sm font-bold text-black mb-1">Company</label>
                  <Input
                    type="text"
                    value={editFields.company}
                    onChange={(e) => updateEditField('company', e.target.value)}
                  />
                </div>
                <div>
                  <label className="block text-sm font-bold text-black mb-1">Location</label>
                  <Input
                    type="text"
                    value={editFields.location}
                    onChange={(e) => updateEditField('location', e.target.value)}
                  />
                </div>
                <div>
                  <label className="block text-sm font-bold text-black mb-1">Apply URL</label>
                  <Input
                    type="url"
                    value={editFields.apply_url}
                    onChange={(e) => updateEditField('apply_url', e.target.value)}
                    placeholder="https://..."
                  />
                </div>
              </div>
            ) : (
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
                <div className="border-2 border-stone-200 p-3">
                  <p className="text-[10px] font-bold text-stone-400 uppercase tracking-wider">Title</p>
                  <p className="text-sm font-bold text-black mt-0.5 truncate">{decodeHtml(job.title) || '--'}</p>
                </div>
                <div className="border-2 border-stone-200 p-3">
                  <p className="text-[10px] font-bold text-stone-400 uppercase tracking-wider">Company</p>
                  <p className="text-sm font-bold text-black mt-0.5 truncate">{decodeHtml(job.company) || '--'}</p>
                </div>
                <div className="border-2 border-stone-200 p-3">
                  <p className="text-[10px] font-bold text-stone-400 uppercase tracking-wider">Location</p>
                  <p className="text-sm text-stone-600 mt-0.5 truncate">{job.location || '--'}</p>
                </div>
                <div className="border-2 border-stone-200 p-3">
                  <p className="text-[10px] font-bold text-stone-400 uppercase tracking-wider">Apply URL</p>
                  {job.apply_url && job.apply_url !== 'Apply' ? (
                    <a href={job.apply_url} target="_blank" rel="noopener noreferrer"
                      className="text-sm text-info hover:underline mt-0.5 block truncate">
                      {job.apply_url}
                    </a>
                  ) : (
                    <p className="text-sm text-stone-400 mt-0.5">--</p>
                  )}
                </div>
              </div>
            )}

            {/* Score cards */}
            <div className="grid grid-cols-3 gap-4 mb-6">
              <div className="border-2 border-black p-4">
                <p className="text-[10px] font-bold text-stone-400 uppercase tracking-wider">ATS</p>
                <p className="text-2xl font-mono font-bold"><ScoreBadge score={job.ats_score} /></p>
              </div>
              <div className="border-2 border-black p-4">
                <p className="text-[10px] font-bold text-stone-400 uppercase tracking-wider">Hiring Manager</p>
                <p className="text-2xl font-mono font-bold"><ScoreBadge score={job.hiring_manager_score} /></p>
              </div>
              <div className="border-2 border-black p-4">
                <p className="text-[10px] font-bold text-stone-400 uppercase tracking-wider">Technical</p>
                <p className="text-2xl font-mono font-bold"><ScoreBadge score={job.tech_recruiter_score} /></p>
              </div>
            </div>
            {/* Metadata row: AI model, source, date */}
            <div className="flex items-center gap-4 mb-6 flex-wrap">
              {job.tailoring_model && (
                <div>
                  <span className="text-[10px] font-bold text-stone-400 uppercase tracking-wider mr-2">AI Model</span>
                  <span className="border border-black bg-stone-900 text-cream font-mono text-[10px] font-bold px-2 py-0.5">
                    {job.tailoring_model}
                  </span>
                </div>
              )}
              {job.source && (
                <div>
                  <span className="text-[10px] font-bold text-stone-400 uppercase tracking-wider mr-2">Source</span>
                  <span className="border border-stone-300 text-stone-500 font-mono text-[10px] font-bold px-2 py-0.5">
                    {job.source}
                  </span>
                </div>
              )}
              {job.first_seen && (
                <div>
                  <span className="text-[10px] font-bold text-stone-400 uppercase tracking-wider mr-2">Found</span>
                  <span className="font-mono text-xs text-stone-600">
                    {new Date(job.first_seen).toLocaleDateString('en-IE', { day: '2-digit', month: 'short', year: 'numeric' })}
                  </span>
                </div>
              )}
            </div>
            {/* Match Analysis */}
            {(job.key_matches?.length > 0 || job.gaps?.length > 0 || job.match_reasoning) && (
              <div className="mb-6 grid grid-cols-1 sm:grid-cols-2 gap-4">
                {job.key_matches?.length > 0 && (
                  <div className="border-2 border-success bg-success-light p-4">
                    <h3 className="text-[10px] font-bold text-success uppercase tracking-wider mb-2">Key Matches</h3>
                    <div className="flex flex-wrap gap-1.5">
                      {job.key_matches.map((m, i) => (
                        <span key={i} className="text-xs font-mono bg-white border border-success text-success px-2 py-0.5">
                          {m}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                {job.gaps?.length > 0 && (
                  <div className="border-2 border-yellow-dark bg-yellow-light p-4">
                    <h3 className="text-[10px] font-bold text-yellow-dark uppercase tracking-wider mb-2">Gaps</h3>
                    <div className="flex flex-wrap gap-1.5">
                      {job.gaps.map((g, i) => (
                        <span key={i} className="text-xs font-mono bg-white border border-yellow-dark text-stone-600 px-2 py-0.5">
                          {g}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                {job.match_reasoning && (
                  <div className="sm:col-span-2 border-2 border-stone-300 bg-stone-50 p-4">
                    <h3 className="text-[10px] font-bold text-stone-400 uppercase tracking-wider mb-1">AI Analysis</h3>
                    <p className="text-xs text-stone-600 leading-relaxed">{job.match_reasoning}</p>
                  </div>
                )}
              </div>
            )}
            {job.description && (
              <div>
                <h3 className="text-xs font-bold text-stone-400 uppercase tracking-wider mb-2">Job Description</h3>
                <p className="text-sm text-stone-700 leading-relaxed whitespace-pre-wrap max-h-[600px] overflow-y-auto">
                  {decodeHtml(job.description)}
                </p>
              </div>
            )}
          </div>
        )}
        {activeTab === 'resume' && (
          <div>
            {job.resume_s3_url ? (
              <div>
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-3">
                    <p className="text-sm text-stone-500">
                      AI Model: <span className="border border-black bg-stone-900 text-cream font-mono text-[10px] font-bold px-2 py-0.5">{job.tailoring_model || '--'}</span>
                    </p>
                    {job.resume_version > 1 && (
                      <span className="text-[10px] font-bold px-1.5 py-0.5 border border-stone-400 bg-stone-100 text-stone-600">v{job.resume_version}</span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <Button
                      variant="secondary"
                      size="sm"
                      loading={regenLoading === 'resume'}
                      disabled={!!regenLoading}
                      onClick={() => handleRegen('resume')}
                    >
                      {regenLoading === 'resume' ? 'Regenerating...' : 'Regenerate'}
                    </Button>
                    <a href={job.resume_s3_url} target="_blank" rel="noopener noreferrer">
                      <Button variant="primary" size="sm">Download PDF</Button>
                    </a>
                  </div>
                </div>
                <div className="border-2 border-black bg-stone-100">
                  <iframe
                    src={job.resume_s3_url}
                    title="Resume PDF Preview"
                    className="w-full bg-white"
                    style={{ height: '700px' }}
                  />
                </div>
              </div>
            ) : (
              <div className="text-center py-16">
                <svg className="w-16 h-16 mx-auto mb-4 text-stone-300" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                <p className="text-stone-400 font-heading font-bold">No resume generated yet</p>
                <p className="text-xs text-stone-400 mt-1 mb-4">Generate a tailored resume for this job.</p>
                <Button
                  variant="accent"
                  size="sm"
                  loading={regenLoading === 'resume'}
                  disabled={!!regenLoading}
                  onClick={() => handleRegen('resume')}
                >
                  {regenLoading === 'resume' ? 'Generating...' : 'Generate Resume'}
                </Button>
              </div>
            )}
          </div>
        )}
        {activeTab === 'cover-letter' && (
          <div>
            {job.cover_letter_s3_url ? (
              <div>
                <div className="flex items-center justify-end gap-2 mb-4">
                  <Button
                    variant="secondary"
                    size="sm"
                    loading={regenLoading === 'cover'}
                    disabled={!!regenLoading}
                    onClick={() => handleRegen('cover')}
                  >
                    {regenLoading === 'cover' ? 'Regenerating...' : 'Regenerate'}
                  </Button>
                  <a href={job.cover_letter_s3_url} target="_blank" rel="noopener noreferrer">
                    <Button variant="primary" size="sm">Download PDF</Button>
                  </a>
                </div>
                <div className="border-2 border-black bg-stone-100">
                  <iframe
                    src={job.cover_letter_s3_url}
                    title="Cover Letter PDF Preview"
                    className="w-full bg-white"
                    style={{ height: '700px' }}
                  />
                </div>
              </div>
            ) : (
              <div className="text-center py-16">
                <svg className="w-16 h-16 mx-auto mb-4 text-stone-300" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                </svg>
                <p className="text-stone-400 font-heading font-bold">No cover letter generated yet</p>
                <p className="text-xs text-stone-400 mt-1">Run the pipeline to generate a cover letter for this job.</p>
              </div>
            )}
          </div>
        )}
        {activeTab === 'contacts' && (
          <ContactsTab job={job} />
        )}
        {activeTab === 'research' && (
          <div>
            <p className="text-stone-400">Company Research — coming in Phase 2D.</p>
          </div>
        )}
        {activeTab === 'prep' && (
          <div>
            <p className="text-stone-400">Interview Prep — coming in Phase 2F.</p>
          </div>
        )}
      </div>
    </div>
  );
}
