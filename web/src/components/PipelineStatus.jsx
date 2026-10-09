import { useState, useEffect, useRef } from 'react';
import { apiGet, apiCall } from '../api';
import Button from './ui/Button';

// Consecutive failed status polls before giving up. One failure is often a
// cold start or a blip; three in a row (15s) is not going to fix itself.
const MAX_POLL_FAILURES = 3;

const STATUS_COLORS = {
  RUNNING: 'bg-yellow border-yellow-dark',
  SUCCEEDED: 'bg-success-light border-success',
  FAILED: 'bg-error-light border-error',
  TIMED_OUT: 'bg-error-light border-error',
  ABORTED: 'bg-stone-100 border-stone-400',
};

const STATUS_LABELS = {
  RUNNING: 'Running',
  SUCCEEDED: 'Complete',
  FAILED: 'Failed',
  TIMED_OUT: 'Timed Out',
  ABORTED: 'Aborted',
};

export default function PipelineStatus({ onComplete }) {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState(null);
  const [pollStatus, setPollStatus] = useState(null);
  // Shown while polling is failing; separate from runError so a transient
  // failure that later recovers can be cleared without touching run errors.
  const [pollError, setPollError] = useState(null);
  // User's configured search queries — fetched from /api/search-config so we
  // don't ship hardcoded keywords that have nothing to do with the user's
  // profile. A run is only possible once they have LOADED and are non-empty:
  // app.py forwards `queries` verbatim into the Step Functions input
  // (PipelineRunRequest's default applies only when the key is absent), so
  // posting [] starts a run that can scrape nothing.
  const [userQueries, setUserQueries] = useState([]);
  // 'loading' | 'loaded' | 'error'
  const [queriesState, setQueriesState] = useState('loading');
  const pollRef = useRef(null);

  useEffect(() => {
    fetchStatus();
    fetchUserQueries();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  async function fetchStatus() {
    try {
      const data = await apiGet('/api/pipeline/status');
      setStatus(data);
    } catch (err) {
      console.error('Pipeline status fetch failed:', err);
    } finally {
      setLoading(false);
    }
  }

  async function fetchUserQueries() {
    try {
      const data = await apiGet('/api/search-config');
      const queries = Array.isArray(data?.queries) ? data.queries.filter(Boolean) : [];
      setUserQueries(queries);
      setQueriesState('loaded');
    } catch (err) {
      // The run button stays disabled: without the user's queries there is
      // nothing correct to send.
      console.warn('Failed to load search config for pipeline run:', err);
      setQueriesState('error');
    }
  }

  const canRun = !running && queriesState === 'loaded' && userQueries.length > 0;

  async function handleRunPipeline() {
    if (!canRun) return;
    setRunning(true);
    setRunError(null);
    setPollError(null);
    setPollStatus('STARTING');

    try {
      const data = await apiCall('/api/pipeline/run', {
        queries: userQueries,
      });

      const execName = data.pollUrl?.split('/').pop();
      if (!execName) throw new Error('No execution ID returned');

      // Poll every 5s
      setPollStatus('RUNNING');
      let failures = 0;
      pollRef.current = setInterval(async () => {
        try {
          const result = await apiGet(`/api/pipeline/status/${execName}`);
          failures = 0;
          setPollError(null);
          setPollStatus(result.status);

          if (result.status !== 'RUNNING') {
            clearInterval(pollRef.current);
            pollRef.current = null;
            setRunning(false);
            if (result.status !== 'SUCCEEDED') {
              // The status endpoint carries the execution's `error` and
              // `cause` for a failed run; either may be absent.
              const detail = [result.error, result.cause].filter(Boolean).join(': ');
              setRunError(`Pipeline ${STATUS_LABELS[result.status] || result.status}${detail ? ` — ${detail}` : ''}`);
            }
            fetchStatus();
            if (onComplete) onComplete();
          }
        } catch (err) {
          console.error('Poll error:', err);
          failures += 1;
          const msg = err?.message || 'request failed';
          if (failures >= MAX_POLL_FAILURES) {
            clearInterval(pollRef.current);
            pollRef.current = null;
            setRunning(false);
            setPollStatus(null);
            setPollError(null);
            setRunError(
              `Stopped checking the pipeline after ${failures} failed status checks (${msg}). ` +
              'It may still be running; reload the page to check again.'
            );
          } else {
            setPollError(`Couldn't check pipeline status (${msg}). Retrying...`);
          }
        }
      }, 5000);
    } catch (err) {
      setRunError(err.message);
      setRunning(false);
      setPollStatus(null);
    }
  }

  if (loading) {
    return (
      <div className="border-2 border-black bg-white px-4 py-3 mb-6 animate-pulse">
        <div className="h-4 bg-stone-200 w-48" />
      </div>
    );
  }

  const latest = status?.latest_run;
  const metrics = status?.today_metrics || [];

  // P1-9: the Step Function routes any step failure to SucceedState via
  // NotifyError (backlog_pipeline_silent_success), so "completed" alone
  // can't be trusted — a run that produced 0 artifacts reports the same
  // terminal status as one that produced 100. This is the one signal the
  // dashboard already has to tell them apart; surface it instead of a plain
  // green "Complete" either way.
  const latestJobsFound = latest ? (latest.raw_jobs ?? latest.jobs_found ?? 0) : 0;
  const latestLooksEmpty = latest?.status === 'completed' && latestJobsFound === 0;

  // Aggregate scraper stats from today's metrics (exclude disabled scrapers)
  const DISABLED_SCRAPERS = ['adzuna', 'glassdoor'];
  const scraperStats = {};
  for (const m of metrics) {
    const name = m.scraper_name || 'unknown';
    if (DISABLED_SCRAPERS.includes(name)) continue;
    if (!scraperStats[name]) scraperStats[name] = { found: 0, matched: 0 };
    scraperStats[name].found += m.jobs_found || 0;
    scraperStats[name].matched += m.jobs_matched || 0;
  }

  return (
    <div className="border-2 border-black bg-white mb-6">
      {/* Header row */}
      <div className="flex items-center justify-between px-4 py-3 border-b-2 border-black">
        <div className="flex items-center gap-3">
          {/* Status dot */}
          {pollStatus === 'RUNNING' ? (
            <span className="inline-block w-2.5 h-2.5 bg-yellow rounded-full animate-pulse" />
          ) : latest ? (
            <span
              className={`inline-block w-2.5 h-2.5 rounded-full ${
                latestLooksEmpty ? 'bg-yellow-dark' :
                latest.status === 'completed' ? 'bg-success' :
                latest.status === 'failed' ? 'bg-error' : 'bg-stone-400'
              }`}
              title={latestLooksEmpty ? 'Completed but found 0 jobs — may be a silent failure, not a quiet day' : undefined}
            />
          ) : (
            <span className="inline-block w-2.5 h-2.5 bg-stone-300 rounded-full" />
          )}

          <div>
            <span className="text-sm font-heading font-bold text-black">
              {pollStatus ? STATUS_LABELS[pollStatus] || pollStatus : 'Pipeline'}
            </span>
            {latest && !pollStatus && (
              <span className="text-xs text-stone-500 ml-2">
                Last run: {new Date(latest.started_at || latest.run_date).toLocaleDateString()} —{' '}
                {latestLooksEmpty ? (
                  <span className="text-yellow-dark font-bold">0 jobs found (check scrapers)</span>
                ) : (
                  <>{latestJobsFound} found, {latest.matched_jobs || latest.jobs_matched || 0} matched</>
                )}
              </span>
            )}
            {pollStatus === 'RUNNING' && (
              <span className="text-xs text-stone-500 ml-2">
                Scraping jobs across all sources...
              </span>
            )}
          </div>
        </div>

        <Button
          variant="accent"
          size="sm"
          onClick={handleRunPipeline}
          loading={running}
          disabled={!canRun}
        >
          {running ? 'Running...' : '▶ Run Pipeline'}
        </Button>
      </div>

      {/* Error */}
      {runError && (
        <div role="alert" className="px-4 py-2 bg-error-light text-error text-xs font-bold">
          {runError}
        </div>
      )}
      {!runError && pollError && (
        <div role="alert" className="px-4 py-2 bg-yellow-light text-stone-700 text-xs font-bold">
          {pollError}
        </div>
      )}
      {!runError && !running && queriesState === 'error' && (
        <div className="px-4 py-2 bg-yellow-light text-stone-700 text-xs">
          Could not load your search queries, so the pipeline cannot run. Reload to try again.
        </div>
      )}
      {!runError && !running && queriesState === 'loaded' && userQueries.length === 0 && (
        <div className="px-4 py-2 bg-yellow-light text-stone-700 text-xs">
          No search queries configured.{' '}
          <a href="/settings" className="font-bold underline">Set them in Settings</a>{' '}
          before running the pipeline.
        </div>
      )}

      {/* Scraper badges (show when we have today's metrics) */}
      {Object.keys(scraperStats).length > 0 && (
        <div className="px-4 py-2 flex flex-wrap gap-2">
          {Object.entries(scraperStats).map(([name, s]) => (
            <span
              key={name}
              className="inline-flex items-center gap-1 px-2 py-0.5 border border-stone-300 bg-stone-50 text-xs font-mono"
            >
              <span className={`w-1.5 h-1.5 rounded-full ${s.found > 0 ? 'bg-success' : 'bg-stone-300'}`} />
              {name}: {s.found}
            </span>
          ))}
        </div>
      )}

      {/* Progress bar when running */}
      {pollStatus === 'RUNNING' && (
        <div className="h-1 bg-stone-200">
          <div className="h-1 bg-yellow animate-[progress_3s_ease-in-out_infinite] w-full origin-left"
               style={{ animation: 'progress 2s ease-in-out infinite' }} />
        </div>
      )}
    </div>
  );
}
