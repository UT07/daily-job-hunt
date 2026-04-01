import { useState, useEffect, useCallback } from 'react';
import { Bell, X } from 'lucide-react';
import { apiGet } from '../api';

// ---- Toast Component ----
function Toast({ message, onDismiss }) {
  useEffect(() => {
    const timer = setTimeout(onDismiss, 5000);
    return () => clearTimeout(timer);
  }, [onDismiss]);

  return (
    <div className="fixed top-4 right-4 z-[100] animate-fade-in">
      <div className="border-2 border-black bg-yellow shadow-brutal p-4 max-w-sm flex items-start gap-3">
        <Bell size={18} className="text-black shrink-0 mt-0.5" />
        <div className="flex-1">
          <p className="text-sm font-heading font-bold text-black">{message}</p>
        </div>
        <button
          onClick={onDismiss}
          className="text-stone-600 hover:text-black transition-colors cursor-pointer shrink-0"
        >
          <X size={16} />
        </button>
      </div>
    </div>
  );
}

// ---- Notification Bell ----
export default function NotificationBell({ collapsed = false }) {
  const [count, setCount] = useState(0);
  const [open, setOpen] = useState(false);
  const [runs, setRuns] = useState([]);
  const [toast, setToast] = useState(null);
  const [lastKnownCount, setLastKnownCount] = useState(0);

  const fetchNotifications = useCallback(async () => {
    try {
      const data = await apiGet('/api/dashboard/runs');
      const runsList = Array.isArray(data) ? data : data.runs || [];

      // Filter to last 24 hours
      const cutoff = Date.now() - 24 * 60 * 60 * 1000;
      const recent = runsList.filter((r) => {
        const ts = r.started_at || r.created_at || r.timestamp;
        return ts && new Date(ts).getTime() > cutoff;
      });

      setRuns(recent);
      setCount(recent.length);

      // Show toast if new runs appeared since last poll
      if (recent.length > lastKnownCount && lastKnownCount > 0) {
        setToast('Pipeline run completed! Check your new jobs.');
      }
      setLastKnownCount(recent.length);
    } catch {
      // Silently fail -- notifications are non-critical
    }
  }, [lastKnownCount]);

  // Poll every 60 seconds
  useEffect(() => {
    fetchNotifications();
    const interval = setInterval(fetchNotifications, 60000);
    return () => clearInterval(interval);
  }, [fetchNotifications]);

  return (
    <>
      {/* Toast */}
      {toast && <Toast message={toast} onDismiss={() => setToast(null)} />}

      {/* Bell button */}
      <div className="relative">
        <button
          onClick={() => setOpen((v) => !v)}
          className={`flex items-center gap-3 px-3 py-2.5 font-heading font-medium text-sm transition-all mb-0.5
            ${collapsed ? 'justify-center' : ''}
            text-stone-500 border-2 border-transparent hover:border-black hover:text-black hover:bg-stone-100`}
          title={collapsed ? `Notifications (${count})` : undefined}
        >
          <div className="relative">
            <Bell size={18} strokeWidth={2.5} />
            {count > 0 && (
              <span className="absolute -top-1.5 -right-1.5 min-w-[16px] h-4 flex items-center justify-center
                bg-error text-white text-[9px] font-mono font-bold border border-black px-0.5">
                {count > 9 ? '9+' : count}
              </span>
            )}
          </div>
          {!collapsed && <span>Notifications</span>}
        </button>

        {/* Dropdown */}
        {open && (
          <div className="absolute left-0 top-full mt-1 z-50 w-72 border-2 border-black bg-white shadow-brutal max-h-80 overflow-y-auto">
            <div className="px-3 py-2 border-b-2 border-black bg-black">
              <p className="text-[11px] font-bold text-cream uppercase tracking-wider">
                Recent Activity ({count})
              </p>
            </div>
            {runs.length === 0 ? (
              <div className="px-3 py-4 text-sm text-stone-400 text-center">
                No recent activity
              </div>
            ) : (
              <div>
                {runs.slice(0, 10).map((run, i) => (
                  <div
                    key={run.id || run.execution_arn || i}
                    className="px-3 py-2.5 border-b border-stone-200 last:border-b-0 hover:bg-yellow-light transition-colors"
                  >
                    <p className="text-xs font-bold text-black">
                      Pipeline {run.status === 'SUCCEEDED' ? 'completed' : run.status === 'FAILED' ? 'failed' : 'ran'}
                    </p>
                    <p className="text-[10px] text-stone-400 font-mono mt-0.5">
                      {run.started_at || run.created_at
                        ? new Date(run.started_at || run.created_at).toLocaleString('en-IE', {
                            day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
                          })
                        : '--'}
                    </p>
                    {run.jobs_found != null && (
                      <p className="text-[10px] text-stone-500 mt-0.5">
                        {run.jobs_found} jobs found
                      </p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </>
  );
}
