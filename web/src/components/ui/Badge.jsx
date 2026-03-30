const VARIANTS = {
  new: 'bg-info-light text-info border-info',
  applied: 'bg-yellow-light text-yellow-dark border-yellow-dark',
  interview: 'bg-success-light text-success border-success',
  offer: 'bg-success text-white border-success',
  rejected: 'bg-error-light text-error border-error',
  withdrawn: 'bg-stone-200 text-stone-600 border-stone-400',
  default: 'bg-stone-200 text-stone-700 border-stone-400',
};

const STATUS_MAP = {
  New: 'new',
  Applied: 'applied',
  Interview: 'interview',
  Offer: 'offer',
  Rejected: 'rejected',
  Withdrawn: 'withdrawn',
};

export default function Badge({ status, children, className = '' }) {
  const key = STATUS_MAP[status] || 'default';
  const v = VARIANTS[key];
  const label = children || status;

  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 font-mono text-[11px] font-bold
        uppercase tracking-wider border-2 ${v} ${className}`}
    >
      {label}
    </span>
  );
}

export function ScoreBadge({ score, className = '' }) {
  if (score == null || score === 0) {
    return <span className="text-stone-400 font-mono text-xs">--</span>;
  }
  const rounded = Math.round(score);
  return (
    <span
      className={`font-mono font-bold text-sm ${
        rounded >= 85 ? 'text-success' : rounded >= 60 ? 'text-yellow-dark' : 'text-error'
      } ${className}`}
    >
      {rounded}
    </span>
  );
}
