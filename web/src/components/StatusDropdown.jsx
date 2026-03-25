import { useState } from 'react';
import { apiPatch } from '../api';

const STATUSES = ['New', 'Applied', 'Interview', 'Offer', 'Rejected', 'Withdrawn'];

const STATUS_STYLES = {
  New: 'text-slate-400 bg-slate-700 border-slate-600',
  Applied: 'text-blue-400 bg-slate-700 border-blue-500/30',
  Interview: 'text-purple-400 bg-slate-700 border-purple-500/30',
  Offer: 'text-emerald-400 bg-slate-700 border-emerald-500/30',
  Rejected: 'text-red-400 bg-slate-700 border-red-500/30',
  Withdrawn: 'text-amber-400 bg-slate-700 border-amber-500/30',
};

export default function StatusDropdown({ jobId, currentStatus, onStatusChange }) {
  const [updating, setUpdating] = useState(false);

  async function handleChange(e) {
    const newStatus = e.target.value;
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

  const style = STATUS_STYLES[currentStatus] || STATUS_STYLES.New;

  return (
    <select
      value={currentStatus}
      onChange={handleChange}
      disabled={updating}
      className={`${style} text-xs font-medium rounded-md px-2 py-1 border
        cursor-pointer focus:ring-2 focus:ring-blue-500 focus:outline-none
        disabled:opacity-50 disabled:cursor-not-allowed appearance-none transition-colors`}
    >
      {STATUSES.map((s) => (
        <option key={s} value={s}>
          {s}
        </option>
      ))}
    </select>
  );
}
