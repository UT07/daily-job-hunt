import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { apiGet, apiPatch } from '../api';

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

  if (!contacts.length) {
    return (
      <div className="text-center py-10">
        <p className="text-stone-400 text-sm mb-2">No contacts found yet.</p>
        <p className="text-xs text-stone-400">
          Use <span className="font-mono font-bold">Add Job → Find Contacts</span> to search for
          LinkedIn contacts at this company.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-xs font-bold text-stone-400 uppercase tracking-wider mb-3">
        {contacts.length} Contact{contacts.length !== 1 ? 's' : ''} · {job.company}
      </p>
      {contacts.map((c, i) => (
        <ContactItem key={i} contact={c} />
      ))}
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

  useEffect(() => {
    async function load() {
      try {
        // Fetch job data from dashboard endpoint and find this job
        const data = await apiGet(`/api/dashboard/jobs?per_page=200`);
        const found = data.jobs?.find((j) => j.job_id === jobId);
        setJob(found || null);
      } catch (err) {
        console.error('Failed to load job:', err);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [jobId]);

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
          <Badge status={job.application_status || 'New'} />
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
                  <p className="text-sm text-stone-500">
                    AI Model: <span className="border border-black bg-stone-900 text-cream font-mono text-[10px] font-bold px-2 py-0.5">{job.tailoring_model || '--'}</span>
                  </p>
                  <a href={job.resume_s3_url} target="_blank" rel="noopener noreferrer">
                    <Button variant="primary" size="sm">Download Resume PDF</Button>
                  </a>
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
                <p className="text-xs text-stone-400 mt-1">Run the pipeline to generate a tailored resume for this job.</p>
              </div>
            )}
          </div>
        )}
        {activeTab === 'cover-letter' && (
          <div>
            {job.cover_letter_s3_url ? (
              <div>
                <div className="flex items-center justify-end mb-4">
                  <a href={job.cover_letter_s3_url} target="_blank" rel="noopener noreferrer">
                    <Button variant="primary" size="sm">Download Cover Letter PDF</Button>
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
