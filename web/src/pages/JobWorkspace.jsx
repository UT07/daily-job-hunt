import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { apiGet } from '../api';
import { ArrowLeft } from 'lucide-react';
import Tabs from '../components/ui/Tabs';
import Button from '../components/ui/Button';
import { ScoreBadge } from '../components/ui/Badge';
import Badge from '../components/ui/Badge';

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
            {job.title}
          </h1>
          <p className="text-sm text-stone-500">
            {job.company} {job.location && `· ${job.location}`}
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
            {job.description && (
              <div>
                <h3 className="text-xs font-bold text-stone-400 uppercase tracking-wider mb-2">Job Description</h3>
                <p className="text-sm text-stone-700 leading-relaxed whitespace-pre-wrap">
                  {job.description.slice(0, 1000)}{job.description.length > 1000 ? '...' : ''}
                </p>
              </div>
            )}
          </div>
        )}
        {activeTab === 'resume' && (
          <div>
            {job.resume_s3_url ? (
              <div>
                <p className="text-sm text-stone-500 mb-4">AI Model: <span className="font-mono font-bold text-black">{job.tailoring_model || '--'}</span></p>
                <a href={job.resume_s3_url} target="_blank" rel="noopener noreferrer">
                  <Button variant="primary" size="sm">Download Resume PDF</Button>
                </a>
              </div>
            ) : (
              <p className="text-stone-400">No tailored resume yet.</p>
            )}
          </div>
        )}
        {activeTab === 'cover-letter' && (
          <div>
            {job.cover_letter_s3_url ? (
              <a href={job.cover_letter_s3_url} target="_blank" rel="noopener noreferrer">
                <Button variant="primary" size="sm">Download Cover Letter PDF</Button>
              </a>
            ) : (
              <p className="text-stone-400">No cover letter yet.</p>
            )}
          </div>
        )}
        {activeTab === 'contacts' && (
          <div>
            <p className="text-stone-400">Contacts view — coming in Phase 2A refinement.</p>
          </div>
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
