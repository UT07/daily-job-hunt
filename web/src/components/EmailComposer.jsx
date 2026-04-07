import { useState } from 'react';
import { apiCall } from '../api';
import Button from './ui/Button';

const TEMPLATES = [
  {
    id: 'cold_outreach',
    label: 'Cold Outreach',
    description: 'First contact — introduce yourself and express interest',
  },
  {
    id: 'follow_up',
    label: 'Follow-Up',
    description: 'After applying — check in after 7+ days of silence',
  },
  {
    id: 'thank_you',
    label: 'Thank You',
    description: 'Post-interview — reinforce fit and express enthusiasm',
  },
];

function daysSince(isoString) {
  if (!isoString) return null;
  const ms = Date.now() - new Date(isoString).getTime();
  return Math.floor(ms / (1000 * 60 * 60 * 24));
}

export default function EmailComposer({ job, defaultContactName = '' }) {
  const [selectedTemplate, setSelectedTemplate] = useState('cold_outreach');
  const [toName, setToName] = useState(defaultContactName);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState(null);
  const [subject, setSubject] = useState('');
  const [emailBody, setEmailBody] = useState('');
  const [copied, setCopied] = useState(false);

  const hasOutput = subject || emailBody;

  const appliedDaysAgo =
    job.application_status === 'Applied' ? daysSince(job.first_seen) : null;
  const showFollowUpBanner = appliedDaysAgo !== null && appliedDaysAgo >= 7;

  async function handleGenerate() {
    setGenerating(true);
    setError(null);
    setSubject('');
    setEmailBody('');
    setCopied(false);
    try {
      const data = await apiCall(
        `/api/dashboard/jobs/${job.job_id}/generate-email`,
        {
          template: selectedTemplate,
          contact_name: toName.trim() || undefined,
        },
      );
      setSubject(data.subject || '');
      setEmailBody(data.body || '');
    } catch (err) {
      setError(err.message || 'Generation failed. Please try again.');
    } finally {
      setGenerating(false);
    }
  }

  function handleCopy() {
    const full = `Subject: ${subject}\n\n${emailBody}`;
    navigator.clipboard.writeText(full).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    });
  }

  return (
    <div className="mt-6 border-2 border-black bg-white">
      {/* Header */}
      <div className="border-b-2 border-black px-4 py-3 bg-black">
        <h3 className="text-xs font-bold text-cream uppercase tracking-wider">
          Email Composer
        </h3>
      </div>

      <div className="p-4 space-y-4">
        {/* Follow-up nudge banner */}
        {showFollowUpBanner && (
          <div className="border-2 border-yellow bg-yellow-light px-4 py-3 flex items-start gap-3">
            <span className="text-yellow-dark font-mono font-bold text-sm shrink-0">!</span>
            <p className="text-xs font-bold text-black">
              It&apos;s been {appliedDaysAgo} day{appliedDaysAgo !== 1 ? 's' : ''} since you
              applied. Consider sending a follow-up.
            </p>
          </div>
        )}

        {/* Template selector */}
        <div>
          <label className="block text-xs font-bold text-stone-400 uppercase tracking-wider mb-2">
            Template
          </label>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            {TEMPLATES.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => setSelectedTemplate(t.id)}
                className={`text-left px-3 py-2.5 border-2 transition-colors cursor-pointer ${
                  selectedTemplate === t.id
                    ? 'border-black bg-yellow-light'
                    : 'border-stone-300 bg-white hover:border-black'
                }`}
              >
                <p className="text-xs font-bold text-black">{t.label}</p>
                <p className="text-[10px] text-stone-500 mt-0.5 leading-snug">
                  {t.description}
                </p>
              </button>
            ))}
          </div>
        </div>

        {/* To field */}
        <div>
          <label
            htmlFor="email-to"
            className="block text-xs font-bold text-stone-400 uppercase tracking-wider mb-1"
          >
            To: (optional)
          </label>
          <input
            id="email-to"
            type="text"
            value={toName}
            onChange={(e) => setToName(e.target.value)}
            placeholder="e.g. Sarah Chen, Hiring Manager"
            className="w-full border-2 border-black bg-white text-sm px-3 py-2 focus:outline-none focus:border-yellow-dark font-mono"
          />
        </div>

        {/* Generate button */}
        <Button
          variant="accent"
          size="sm"
          loading={generating}
          disabled={generating}
          onClick={handleGenerate}
        >
          {generating ? 'Generating...' : 'Generate Email'}
        </Button>

        {/* Error */}
        {error && (
          <p className="text-xs text-red-600 font-mono border-2 border-red-300 bg-red-50 px-3 py-2">
            {error}
          </p>
        )}

        {/* Output */}
        {hasOutput && (
          <div className="space-y-3 border-2 border-black p-4 bg-stone-50">
            {/* Subject */}
            <div>
              <label className="block text-xs font-bold text-stone-400 uppercase tracking-wider mb-1">
                Subject
              </label>
              <input
                type="text"
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                className="w-full border-2 border-black bg-white text-sm px-3 py-2 focus:outline-none focus:border-yellow-dark font-mono"
              />
            </div>

            {/* Body */}
            <div>
              <label className="block text-xs font-bold text-stone-400 uppercase tracking-wider mb-1">
                Body
              </label>
              <textarea
                value={emailBody}
                onChange={(e) => setEmailBody(e.target.value)}
                rows={12}
                className="w-full border-2 border-black bg-white text-sm px-3 py-2 focus:outline-none focus:border-yellow-dark font-mono resize-y leading-relaxed"
              />
            </div>

            {/* Copy button */}
            <button
              type="button"
              onClick={handleCopy}
              className={`text-xs font-bold border-2 border-black px-4 py-2 transition-colors cursor-pointer ${
                copied
                  ? 'bg-black text-cream'
                  : 'bg-white text-black hover:bg-yellow-light'
              }`}
            >
              {copied ? 'Copied to Clipboard!' : 'Copy Full Email'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
