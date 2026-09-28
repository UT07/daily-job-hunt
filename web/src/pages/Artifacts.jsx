import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, CheckCircle2, Download, FileText, Mail } from 'lucide-react';
import { apiGet } from '../api';
import { ARTIFACT_KINDS, artifactStatus, summarise } from '../lib/artifactPolicy';

// Only tiers the policy applies to. Requesting C/D would fetch hundreds of
// rows this page has nothing to say about.
const TIERS = ['S', 'A', 'B'];
const TIER_LABEL = { S: 'Must Apply', A: 'Strong Match', B: 'Worth a Look' };
const KIND_ICON = { resume: FileText, cover_letter: Mail };

function ArtifactLink({ job, kind }) {
  const Icon = KIND_ICON[kind];
  const { label, urlField } = ARTIFACT_KINDS[kind];
  const href = job[urlField];
  const present = artifactStatus(job).present.includes(kind);

  if (!present) {
    return (
      <span
        className="inline-flex items-center gap-1.5 border-2 border-dashed border-stone-300 px-2.5 py-1 text-xs font-mono text-stone-400"
        title={`No ${label.toLowerCase()} generated yet`}
      >
        <Icon size={13} aria-hidden="true" />
        {label} missing
      </span>
    );
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1.5 border-2 border-black bg-white px-2.5 py-1 text-xs font-mono hover:bg-stone-50 shadow-brutal-sm"
    >
      <Icon size={13} aria-hidden="true" />
      {label}
      <Download size={11} aria-hidden="true" />
    </a>
  );
}

export default function Artifacts() {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // lifecycle=active for the same reason the dashboard list uses it: the
      // backend's not_archived default means age < 30 and would also return
      // the 14-30 day stale band.
      const params = new URLSearchParams({
        page: '1', per_page: '100', tier: TIERS.join(','), lifecycle: 'active',
      });
      const data = await apiGet(`/api/dashboard/jobs?${params}`);
      setJobs(data.jobs || []);
    } catch (e) {
      setError(e?.message || 'Could not load artifacts');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const stats = summarise(jobs);

  return (
    <div className="p-6 max-w-5xl">
      <h1 className="text-2xl font-heading font-bold mb-1">Artifacts</h1>
      <p className="text-sm text-stone-500 mb-5">
        Tailored resumes and cover letters for your top matches.
        S and A tier get both; B gets a resume.
      </p>

      {loading && <p className="font-mono text-sm text-stone-400">Loading…</p>}

      {error && (
        <div role="alert" className="border-2 border-black bg-warning-light p-4 font-mono text-sm">
          {error}{' '}
          <button onClick={load} className="underline font-bold">Retry</button>
        </div>
      )}

      {!loading && !error && (
        <>
          <div className="border-2 border-black shadow-brutal bg-white p-4 mb-6 flex gap-8 items-center">
            <div>
              <div className="text-3xl font-bold font-heading">{stats.complete}<span className="text-stone-300">/{stats.total}</span></div>
              <div className="text-[10px] uppercase tracking-wider font-mono text-stone-400">Ready to send</div>
            </div>
            {stats.incomplete > 0 && (
              <div className="flex items-center gap-2 text-sm font-mono text-warning-dark">
                <AlertTriangle size={16} aria-hidden="true" />
                {stats.incomplete} incomplete
              </div>
            )}
            {stats.total > 0 && stats.incomplete === 0 && (
              <div className="flex items-center gap-2 text-sm font-mono text-success-dark">
                <CheckCircle2 size={16} aria-hidden="true" />
                All set
              </div>
            )}
          </div>

          {stats.total === 0 && (
            <p className="font-mono text-sm text-stone-500">
              No S, A or B tier jobs in the last 14 days — nothing to generate yet.
            </p>
          )}

          {TIERS.map((tier) => {
            const rows = jobs.filter((j) => j.score_tier === tier);
            if (rows.length === 0) return null;
            return (
              <section key={tier} className="mb-7">
                <h2 className="text-xs font-mono uppercase tracking-wider text-stone-500 mb-2">
                  {tier} — {TIER_LABEL[tier]} ({rows.length})
                </h2>
                <ul className="space-y-2">
                  {rows.map((job) => {
                    const st = artifactStatus(job);
                    return (
                      <li
                        key={job.job_id || job.job_hash}
                        className="border-2 border-black bg-white p-3 flex flex-wrap items-center gap-3 justify-between"
                      >
                        <div className="min-w-0">
                          <Link
                            to={`/jobs/${job.job_id || job.job_hash}`}
                            className="font-bold text-sm hover:underline"
                          >
                            {job.title || 'Untitled role'}
                          </Link>
                          <div className="text-xs text-stone-500 font-mono truncate">
                            {job.company} · score {job.match_score ?? '—'}
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          {st.expected.map((kind) => (
                            <ArtifactLink key={kind} job={job} kind={kind} />
                          ))}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </section>
            );
          })}
        </>
      )}
    </div>
  );
}
