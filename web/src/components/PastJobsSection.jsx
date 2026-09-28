/**
 * "Past / Outdated" — the 14-to-30-day shelf.
 *
 * The owner's rule (2026-09-28): under 14 days is the working dashboard,
 * 14-30 days is past/outdated but still reachable, 30+ is off the board
 * entirely, and anything the user applied to is exempt forever. The backend
 * already implements all four (db_client.get_jobs `lifecycle` filter); this is
 * the only place the middle bucket becomes visible.
 *
 * Deliberately NOT a peer tab of the S/A/B tier tabs: those swap out the one
 * list, and "past" needs to read as a footnote *under* the live jobs rather
 * than as another way of slicing them. Collapsed by default, muted, and it
 * renders nothing at all when the bucket is empty — a permanently visible
 * "Past / Outdated (0)" header is exactly the clutter this feature exists to
 * remove.
 *
 * Inherits the dashboard's other filters (tier, score, expiry, ...) so the two
 * lists are the same query differing only in age; the active filter chips above
 * already tell the user what's applied.
 */
import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronDown, ChevronRight, History } from 'lucide-react';
import { apiGet } from '../api';
import { decodeHtml } from './JobTable';
import { formatJobAge, STALE_AFTER_DAYS, ARCHIVE_AFTER_DAYS } from '../lib/jobAge';
import { buildJobQueryParams } from '../lib/jobQuery';

const PER_PAGE = 25;

function PastJobRow({ job, onOpen }) {
  const age = formatJobAge(job.first_seen);
  return (
    <li>
      <button
        type="button"
        onClick={() => onOpen(job.job_id)}
        className="w-full text-left flex items-center gap-3 px-3 py-2 border-b border-stone-200
          last:border-b-0 hover:bg-white transition-colors cursor-pointer"
      >
        <span className="min-w-0 flex-1">
          <span className="block text-xs font-heading font-bold text-stone-600 truncate">
            {decodeHtml(job.title)}
          </span>
          <span className="block text-[11px] text-stone-400 truncate">
            {decodeHtml(job.company)}
            {job.location && ` · ${job.location}`}
          </span>
        </span>
        {age && (
          <span className="shrink-0 font-mono text-[10px] uppercase tracking-wider text-stone-500
            border border-stone-300 bg-white px-1.5 py-0.5">
            {age}
          </span>
        )}
        {job.score_tier && (
          <span className="shrink-0 w-5 h-5 text-[10px] font-bold leading-5 text-center
            border border-stone-300 bg-stone-100 text-stone-500">
            {job.score_tier}
          </span>
        )}
        <span className="shrink-0 font-mono text-xs font-bold text-stone-400 w-8 text-right">
          {job.match_score ?? '--'}
        </span>
      </button>
    </li>
  );
}

export default function PastJobsSection({ filters, filterVersion = 0 }) {
  const navigate = useNavigate();
  const [expanded, setExpanded] = useState(false);
  const [jobs, setJobs] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchStale = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = buildJobQueryParams(filters, {
        page: 1,
        perPage: PER_PAGE,
        lifecycle: 'stale',
      });
      const data = await apiGet(`/api/dashboard/jobs?${params.toString()}`);
      setJobs(data.jobs || []);
      setTotal(typeof data.total === 'number' ? data.total : (data.jobs?.length || 0));
    } catch (err) {
      // A failure here must not take the active list down with it: this is a
      // secondary shelf, so it degrades to a one-line notice.
      setError(err.message);
      setJobs([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
    // `filters` is read through the callback body but intentionally not a dep:
    // the parent rebuilds that object on every keystroke, while a refetch
    // should only happen when filters are actually *applied*. filterVersion is
    // that signal — the same contract fetchJobs uses in Dashboard.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterVersion]);

  useEffect(() => { fetchStale(); }, [fetchStale]);

  // Nothing to say while the first request is in flight, and nothing to say
  // when the bucket is empty. Both render null rather than a placeholder.
  if (loading && jobs.length === 0 && !error) return null;

  if (error) {
    return (
      <div className="mt-8 text-[11px] text-stone-400">
        Couldn&apos;t load past / outdated jobs: {error}
      </div>
    );
  }

  if (total === 0) return null;

  return (
    <section className="mt-8 opacity-75 hover:opacity-100 transition-opacity">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="w-full flex items-center gap-2 px-3 py-2 border border-stone-300 border-dashed
          bg-stone-50 text-left cursor-pointer hover:bg-stone-100 transition-colors"
      >
        {expanded
          ? <ChevronDown size={14} className="text-stone-400 shrink-0" />
          : <ChevronRight size={14} className="text-stone-400 shrink-0" />}
        <History size={14} className="text-stone-400 shrink-0" />
        <span className="text-xs font-heading font-bold text-stone-500 uppercase tracking-wider">
          Past / Outdated
        </span>
        <span className="font-mono text-xs text-stone-400">({total})</span>
        <span className="text-[11px] text-stone-400 font-normal normal-case tracking-normal ml-auto">
          {STALE_AFTER_DAYS}&ndash;{ARCHIVE_AFTER_DAYS} days old &middot; archived after {ARCHIVE_AFTER_DAYS}
        </span>
      </button>

      {expanded && (
        <div className="border border-t-0 border-stone-300 border-dashed bg-stone-50">
          <ul>
            {jobs.map((job) => (
              <PastJobRow key={job.job_id} job={job} onOpen={(id) => navigate(`/jobs/${id}`)} />
            ))}
          </ul>
          {total > jobs.length && (
            <p className="px-3 py-2 text-[11px] text-stone-400 border-t border-stone-200">
              Showing {jobs.length} of {total}. Jobs leave this list for good once they pass{' '}
              {ARCHIVE_AFTER_DAYS} days &mdash; unless you applied.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
