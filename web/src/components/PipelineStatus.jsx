import { useState, useEffect, useRef } from 'react';
import { apiGet, apiCall } from '../api';
import Button from './ui/Button';

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
  const pollRef = useRef(null);

  useEffect(() => {
    fetchStatus();
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

  async function handleRunPipeline() {
    setRunning(true);
    setRunError(null);
    setPollStatus('STARTING');

    try {
      const data = await apiCall('/api/pipeline/run', {
        queries: ['software engineer', 'python developer', 'backend developer'],
      });

      const execName = data.pollUrl?.split('/').pop();
      if (!execName) throw new Error('No execution ID returned');

      // Poll every 5s
      setPollStatus('RUNNING');
      pollRef.current = setInterval(async () => {
        try {
          const result = await apiGet(`/api/pipeline/status/${execName}`);
          setPollStatus(result.status);

          if (result.status !== 'RUNNING') {
            clearInterval(pollRef.current);
            pollRef.current = null;
            setRunning(false);
            fetchStatus();
            if (onComplete) onComplete();
          }
        } catch (err) {
          console.error('Poll error:', err);
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
            <span className={`inline-block w-2.5 h-2.5 rounded-full ${
              latest.status === 'completed' ? 'bg-success' :
              latest.status === 'failed' ? 'bg-error' : 'bg-stone-400'
            }`} />
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
                {latest.jobs_found || 0} found, {latest.jobs_matched || 0} matched
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
          disabled={running}
        >
          {running ? 'Running...' : '▶ Run Pipeline'}
        </Button>
      </div>

      {/* Error */}
      {runError && (
        <div className="px-4 py-2 bg-error-light text-error text-xs font-bold">
          {runError}
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
