import { useState, useRef, useEffect } from 'react';
import { apiPatch } from '../api';

const STATUSES = ['New', 'Applied', 'Interview', 'Offer', 'Rejected', 'Withdrawn'];

const STATUS_STYLES = {
  New:       'bg-info-light text-info border-info',
  Applied:   'bg-yellow-light text-yellow-dark border-yellow-dark',
  Interview: 'bg-success-light text-success border-success',
  Offer:     'bg-success text-white border-success',
  Rejected:  'bg-error-light text-error border-error',
  Withdrawn: 'bg-stone-200 text-stone-600 border-stone-400',
};

export default function StatusDropdown({ jobId, currentStatus, onStatusChange }) {
  const [open, setOpen] = useState(false);
  const [updating, setUpdating] = useState(false);
  const ref = useRef(null);

  // Close on outside click
  useEffect(() => {
    function handleClickOutside(e) {
      if (ref.current && !ref.current.contains(e.target)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  async function handleSelect(newStatus) {
    setOpen(false);
    if (newStatus === currentStatus) return;
    setUpdating(true);
    try {
      await apiPatch(`/api/dashboard/jobs/${encodeURIComponent(jobId)}`, {
        application_status: newStatus,
      });
      onStatusChange(jobId, newStatus);
    } catch (err) {
      console.error('Failed to update status:', err);
    } finally {
      setUpdating(false);
    }
  }

  const badgeClass = STATUS_STYLES[currentStatus] || STATUS_STYLES.New;

  return (
    <div ref={ref} className="relative inline-block">
      {/* Trigger button — looks like a badge */}
      <button
        onClick={() => !updating && setOpen((o) => !o)}
        disabled={updating}
        className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 border-2 font-mono text-[11px]
          font-bold uppercase tracking-wider cursor-pointer select-none
          transition-shadow hover:shadow-brutal-sm
          disabled:opacity-50 disabled:cursor-not-allowed
          ${badgeClass}`}
      >
        {updating ? '…' : currentStatus}
        <span className="text-[8px] leading-none opacity-60">▼</span>
      </button>

      {/* Dropdown panel */}
      {open && (
        <div className="absolute top-full left-0 mt-1 z-50 min-w-[130px]
          bg-white border-2 border-black shadow-brutal py-1">
          {STATUSES.map((s) => {
            const sc = STATUS_STYLES[s] || STATUS_STYLES.New;
            const isActive = s === currentStatus;
            return (
              <button
                key={s}
                onClick={() => handleSelect(s)}
                className={`w-full flex items-center gap-2 px-3 py-1.5 text-left
                  font-mono text-[11px] font-bold uppercase tracking-wider
                  hover:bg-stone-100 transition-colors
                  ${isActive ? 'bg-stone-50' : ''}`}
              >
                <span className={`inline-block w-2 h-2 border ${sc}`} />
                {s}
                {isActive && <span className="ml-auto text-[8px]">✓</span>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
