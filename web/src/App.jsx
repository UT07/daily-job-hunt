import { useState } from 'react';
import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom';
import AuthProvider from './auth/AuthProvider';
import { useAuth } from './auth/useAuth';
import LoginPage from './pages/LoginPage';
import Dashboard from './pages/Dashboard';
import Onboarding from './pages/Onboarding';
import Settings from './pages/Settings';
import Privacy from './pages/Privacy';
import DataExport from './pages/DataExport';
import { apiCall } from './api';
import ScoreCard from './components/ScoreCard';
import TailorCard from './components/TailorCard';
import CoverLetterCard from './components/CoverLetterCard';
import ContactsCard from './components/ContactsCard';
import ErrorBanner from './components/ErrorBanner';
import ConsentBanner from './components/ConsentBanner';

function ActionButton({ onClick, loading, color, children }) {
  const colors = {
    blue: 'bg-blue-600 hover:bg-blue-500 focus:ring-blue-400 shadow-lg shadow-blue-500/20',
    emerald: 'bg-emerald-600 hover:bg-emerald-500 focus:ring-emerald-400 shadow-lg shadow-emerald-500/20',
    purple: 'bg-purple-600 hover:bg-purple-500 focus:ring-purple-400 shadow-lg shadow-purple-500/20',
    orange: 'bg-orange-600 hover:bg-orange-500 focus:ring-orange-400 shadow-lg shadow-orange-500/20',
  };

  return (
    <button
      onClick={onClick}
      disabled={loading}
      className={`${colors[color]} text-white px-5 py-2.5 rounded-lg text-sm font-medium transition
        focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-slate-900
        disabled:opacity-50 disabled:cursor-not-allowed
        inline-flex items-center gap-2`}
    >
      {loading && <span className="spinner" />}
      {children}
    </button>
  );
}

function NavLink({ to, children }) {
  const location = useLocation();
  const isActive = location.pathname === to;
  return (
    <Link
      to={to}
      className={`text-sm font-medium px-3 py-1.5 rounded-lg transition ${
        isActive
          ? 'bg-slate-700 text-white'
          : 'text-slate-400 hover:text-white'
      }`}
    >
      {children}
    </Link>
  );
}

function AppContent() {
  const { user, loading, signOut } = useAuth();
  const [jd, setJd] = useState('');
  const [jobTitle, setJobTitle] = useState('Software Engineer');
  const [company, setCompany] = useState('');
  const [resumeType, setResumeType] = useState('sre_devops');
  const [results, setResults] = useState([]);
  const [actionLoading, setActionLoading] = useState({});

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center">
        <div className="text-slate-400 text-sm">Loading...</div>
      </div>
    );
  }

  if (!user) {
    return <LoginPage />;
  }

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
    setActionLoading((prev) => ({ ...prev, [key]: true }));
    try {
      const data = await apiCall(endpoint, payload);
      addResult({ type: cardType, data, company: payload.company });
    } catch (e) {
      addResult({ type: 'error', message: `${cardType} failed: ${e.message}` });
    } finally {
      setActionLoading((prev) => ({ ...prev, [key]: false }));
    }
  }

  return (
    <div className="min-h-screen bg-slate-900 text-slate-100">
      {/* Header */}
      <header className="bg-slate-800 border-b border-slate-700 sticky top-0 z-10">
        <div className="max-w-4xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-2xl">🎯</span>
            <h1 className="text-xl font-bold text-white">Job Hunt</h1>
          </div>
          <nav className="hidden sm:flex items-center gap-1">
            <NavLink to="/dashboard">Dashboard</NavLink>
            <NavLink to="/">Tailor</NavLink>
            <NavLink to="/settings">Settings</NavLink>
          </nav>
          <div className="flex items-center gap-4">
            <span className="text-sm text-slate-400 hidden sm:block">{user.email}</span>
            <button
              onClick={signOut}
              className="text-sm text-slate-400 hover:text-white font-medium transition"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-4 py-8">
        {/* Input Card */}
        <div className="bg-slate-800 rounded-xl shadow-lg border border-slate-700 p-6 mb-6">
          <label className="block text-sm font-medium text-slate-300 mb-2">
            Job Description
          </label>
          <textarea
            value={jd}
            onChange={(e) => setJd(e.target.value)}
            rows={8}
            placeholder="Paste the full job description here..."
            className="w-full bg-slate-700/50 border border-slate-600 rounded-lg px-4 py-3 text-sm text-white
              focus:ring-2 focus:ring-blue-500 focus:border-blue-500 resize-y
              placeholder:text-slate-500"
          />

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mt-4">
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">Job Title</label>
              <input
                value={jobTitle}
                onChange={(e) => setJobTitle(e.target.value)}
                className="w-full bg-slate-700/50 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white
                  focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">Company</label>
              <input
                value={company}
                onChange={(e) => setCompany(e.target.value)}
                placeholder="e.g. Google"
                className="w-full bg-slate-700/50 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white
                  placeholder:text-slate-500 focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">Resume Type</label>
              <select
                value={resumeType}
                onChange={(e) => setResumeType(e.target.value)}
                className="w-full bg-slate-700/50 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white
                  focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
              >
                <option value="sre_devops">SRE / DevOps Engineer</option>
                <option value="fullstack">Full-Stack Software Engineer</option>
              </select>
            </div>
          </div>
        </div>

        {/* Action Buttons */}
        <div className="flex flex-wrap gap-3 mb-8">
          <ActionButton color="blue" loading={actionLoading.score} onClick={() => run('score', '/api/score', 'score')}>
            Score Resume
          </ActionButton>
          <ActionButton color="emerald" loading={actionLoading.tailor} onClick={() => run('tailor', '/api/tailor', 'tailor')}>
            Tailor Resume
          </ActionButton>
          <ActionButton color="purple" loading={actionLoading.cover} onClick={() => run('cover', '/api/cover-letter', 'cover')}>
            Generate Cover Letter
          </ActionButton>
          <ActionButton color="orange" loading={actionLoading.contacts} onClick={() => run('contacts', '/api/contacts', 'contacts')}>
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
      <footer className="border-t border-slate-700 mt-12">
        <div className="max-w-4xl mx-auto px-4 py-4 flex items-center justify-center gap-4 text-xs text-slate-500">
          <span>Built by Utkarsh Singh — FastAPI + React + Tailwind</span>
          <span className="text-slate-600">|</span>
          <Link to="/privacy" className="hover:text-slate-300 transition">Privacy</Link>
          <Link to="/data-export" className="hover:text-slate-300 transition">Export Data</Link>
        </div>
      </footer>

      <ConsentBanner />
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/" element={<AppContent />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/onboarding" element={<Onboarding />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/privacy" element={<Privacy />} />
          <Route path="/data-export" element={<DataExport />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
