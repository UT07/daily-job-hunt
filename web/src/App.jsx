import { useState } from 'react';
import { apiCall } from './api';
import ScoreCard from './components/ScoreCard';
import TailorCard from './components/TailorCard';
import CoverLetterCard from './components/CoverLetterCard';
import ContactsCard from './components/ContactsCard';
import ErrorBanner from './components/ErrorBanner';

function ActionButton({ onClick, loading, color, children }) {
  const colors = {
    blue: 'bg-blue-600 hover:bg-blue-700 focus:ring-blue-300',
    emerald: 'bg-emerald-600 hover:bg-emerald-700 focus:ring-emerald-300',
    purple: 'bg-purple-600 hover:bg-purple-700 focus:ring-purple-300',
    orange: 'bg-orange-600 hover:bg-orange-700 focus:ring-orange-300',
  };

  return (
    <button
      onClick={onClick}
      disabled={loading}
      className={`${colors[color]} text-white px-5 py-2.5 rounded-lg text-sm font-medium transition
        focus:outline-none focus:ring-2 focus:ring-offset-2
        disabled:opacity-50 disabled:cursor-not-allowed
        inline-flex items-center gap-2`}
    >
      {loading && <span className="spinner" />}
      {children}
    </button>
  );
}

export default function App() {
  const [jd, setJd] = useState('');
  const [jobTitle, setJobTitle] = useState('Software Engineer');
  const [company, setCompany] = useState('');
  const [resumeType, setResumeType] = useState('sre_devops');
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState({});

  function getPayload() {
    if (jd.trim().length < 20) {
      addResult({ type: 'error', message: 'Please paste a job description (at least 20 characters).' });
      return null;
    }
    return {
      job_description: jd.trim(),
      job_title: jobTitle || 'Software Engineer',
      company: company || 'Unknown',
      resume_type: resumeType,
    };
  }

  function addResult(result) {
    setResults((prev) => [result, ...prev]);
  }

  async function run(key, endpoint, cardType) {
    const payload = getPayload();
    if (!payload) return;
    setLoading((prev) => ({ ...prev, [key]: true }));
    try {
      const data = await apiCall(endpoint, payload);
      addResult({ type: cardType, data, company: payload.company });
    } catch (e) {
      addResult({ type: 'error', message: `${cardType} failed: ${e.message}` });
    } finally {
      setLoading((prev) => ({ ...prev, [key]: false }));
    }
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-4xl mx-auto px-4 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-2xl">🎯</span>
            <h1 className="text-xl font-bold text-gray-900">Job Hunt</h1>
          </div>
          <span className="text-sm text-gray-500 hidden sm:block">AI-powered resume tailoring</span>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-4 py-8">
        {/* Input Card */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6 mb-6">
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Job Description
          </label>
          <textarea
            value={jd}
            onChange={(e) => setJd(e.target.value)}
            rows={8}
            placeholder="Paste the full job description here..."
            className="w-full border border-gray-300 rounded-lg px-4 py-3 text-sm
              focus:ring-2 focus:ring-blue-500 focus:border-blue-500 resize-y
              placeholder:text-gray-400"
          />

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mt-4">
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">Job Title</label>
              <input
                value={jobTitle}
                onChange={(e) => setJobTitle(e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">Company</label>
              <input
                value={company}
                onChange={(e) => setCompany(e.target.value)}
                placeholder="e.g. Google"
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">Resume Type</label>
              <select
                value={resumeType}
                onChange={(e) => setResumeType(e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 bg-white"
              >
                <option value="sre_devops">SRE / DevOps Engineer</option>
                <option value="fullstack">Full-Stack Software Engineer</option>
              </select>
            </div>
          </div>
        </div>

        {/* Action Buttons */}
        <div className="flex flex-wrap gap-3 mb-8">
          <ActionButton color="blue" loading={loading.score} onClick={() => run('score', '/api/score', 'score')}>
            Score Resume
          </ActionButton>
          <ActionButton color="emerald" loading={loading.tailor} onClick={() => run('tailor', '/api/tailor', 'tailor')}>
            Tailor Resume
          </ActionButton>
          <ActionButton color="purple" loading={loading.cover} onClick={() => run('cover', '/api/cover-letter', 'cover')}>
            Generate Cover Letter
          </ActionButton>
          <ActionButton color="orange" loading={loading.contacts} onClick={() => run('contacts', '/api/contacts', 'contacts')}>
            Find LinkedIn Contacts
          </ActionButton>
        </div>

        {/* Results */}
        <div className="space-y-4">
          {results.map((r, i) => {
            switch (r.type) {
              case 'score':
                return <ScoreCard key={i} data={r.data} company={r.company} />;
              case 'tailor':
                return <TailorCard key={i} data={r.data} company={r.company} />;
              case 'cover':
                return <CoverLetterCard key={i} data={r.data} company={r.company} />;
              case 'contacts':
                return <ContactsCard key={i} data={r.data} company={r.company} />;
              case 'error':
                return <ErrorBanner key={i} message={r.message} />;
              default:
                return null;
            }
          })}
        </div>
      </main>

      {/* Footer */}
      <footer className="border-t border-gray-200 mt-12">
        <div className="max-w-4xl mx-auto px-4 py-4 text-center text-xs text-gray-400">
          Built by Utkarsh Singh — FastAPI + React + Tailwind
        </div>
      </footer>
    </div>
  );
}
