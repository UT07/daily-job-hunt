import { useState } from 'react';
import { apiCall } from '../api';
import Button from '../components/ui/Button';
import Input, { Textarea, Select } from '../components/ui/Input';
import ScoreCard from '../components/ScoreCard';
import TailorCard from '../components/TailorCard';
import CoverLetterCard from '../components/CoverLetterCard';
import ContactsCard from '../components/ContactsCard';
import ErrorBanner from '../components/ErrorBanner';

export default function AddJob() {
  const [jd, setJd] = useState('');
  const [jobTitle, setJobTitle] = useState('Software Engineer');
  const [company, setCompany] = useState('');
  const [resumeType, setResumeType] = useState('sre_devops');
  const [results, setResults] = useState([]);
  const [actionLoading, setActionLoading] = useState({});

  function getPayload() {
    return {
      job_description: jd,
      job_title: jobTitle,
      company,
      resume_type: resumeType,
    };
  }

  function addResult(type, data) {
    setResults((prev) => [{ type, data, company }, ...prev]);
  }

  async function run(endpoint, key) {
    if (!jd.trim()) return;
    setActionLoading((prev) => ({ ...prev, [key]: true }));
    try {
      const payload = getPayload();
      const data = await apiCall(endpoint, payload);
      addResult(key, data);
    } catch (err) {
      addResult('error', { message: err.message });
    } finally {
      setActionLoading((prev) => ({ ...prev, [key]: false }));
    }
  }

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
        <div className="mb-4">
          <Textarea
            label="Job Description"
            id="jd"
            rows={8}
            placeholder="Paste the full job description here..."
            value={jd}
            onChange={(e) => setJd(e.target.value)}
          />
        </div>

        {/* Three inputs in a row */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
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

        {/* Action buttons */}
        <div className="flex flex-wrap gap-3">
          <Button
            variant="secondary"
            loading={actionLoading.score}
            disabled={!jd.trim()}
            onClick={() => run('/api/score', 'score')}
          >
            Score Resume
          </Button>
          <Button
            variant="accent"
            loading={actionLoading.tailor}
            disabled={!jd.trim()}
            onClick={() => run('/api/tailor', 'tailor')}
          >
            Tailor Resume
          </Button>
          <Button
            variant="secondary"
            loading={actionLoading['cover-letter']}
            disabled={!jd.trim()}
            onClick={() => run('/api/cover-letter', 'cover-letter')}
          >
            Cover Letter
          </Button>
          <Button
            variant="secondary"
            loading={actionLoading.contacts}
            disabled={!jd.trim()}
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
            if (result.type === 'error') {
              return <ErrorBanner key={i} message={result.data.message} />;
            }
            return null;
          })}
        </div>
      )}
    </div>
  );
}
