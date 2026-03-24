import { useState } from 'react';
import { apiPatch } from '../api';

const STATUSES = ['New', 'Applied', 'Interview', 'Offer', 'Rejected', 'Withdrawn'];

const STATUS_COLORS = {
  New: 'bg-gray-100 text-gray-700 border-gray-300',
  Applied: 'bg-blue-100 text-blue-700 border-blue-300',
  Interview: 'bg-purple-100 text-purple-700 border-purple-300',
  Offer: 'bg-emerald-100 text-emerald-700 border-emerald-300',
  Rejected: 'bg-red-100 text-red-700 border-red-300',
  Withdrawn: 'bg-amber-100 text-amber-700 border-amber-300',
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

  const colorClass = STATUS_COLORS[currentStatus] || STATUS_COLORS.New;

  return (
    <select
      value={currentStatus}
      onChange={handleChange}
      disabled={updating}
      className={`${colorClass} text-xs font-medium rounded-md px-2 py-1 border
        cursor-pointer focus:ring-2 focus:ring-blue-500 focus:outline-none
        disabled:opacity-50 disabled:cursor-not-allowed appearance-none`}
    >
      {STATUSES.map((s) => (
        <option key={s} value={s}>
          {s}
        </option>
      ))}
    </select>
  );
}
