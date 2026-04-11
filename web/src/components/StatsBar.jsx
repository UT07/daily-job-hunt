import { Briefcase, CheckCircle, Users, TrendingUp, XCircle, Award, Trash2 } from 'lucide-react';
import KPICard from './ui/KPICard';

/**
 * Dynamic stats bar — always shows Total + Avg Score, then shows
 * Applied / Interviewing / Offers / Rejected / Withdrawn only if their
 * count is > 0. Cards appear as the user updates application statuses.
 */
export default function StatsBar({ stats }) {
  const avgScore = Math.round(stats.avg_match_score ?? 0);

  // Start with the two always-visible cards
  const cards = [
    {
      key: 'total',
      label: 'Total Jobs',
      value: stats.total_jobs ?? 0,
      icon: <Briefcase size={20} />,
    },
    {
      key: 'avg_score',
      label: 'Avg Score',
      value: avgScore,
      icon: <TrendingUp size={20} />,
      deltaColor: avgScore >= 85 ? 'text-success' : avgScore >= 60 ? 'text-yellow-dark' : 'text-error',
    },
  ];

  // Dynamic cards — only shown if > 0
  const dynamicCards = [
    {
      key: 'applied',
      label: 'Applied',
      value: stats.total_applied ?? 0,
      icon: <CheckCircle size={20} />,
    },
    {
      key: 'interviewing',
      label: 'Interviewing',
      value: stats.total_interviewing ?? 0,
      icon: <Users size={20} />,
    },
    {
      key: 'offers',
      label: 'Offers',
      value: stats.total_offers ?? 0,
      icon: <Award size={20} />,
    },
    {
      key: 'rejected',
      label: 'Rejected',
      value: stats.total_rejected ?? 0,
      icon: <XCircle size={20} />,
    },
    {
      key: 'withdrawn',
      label: 'Withdrawn',
      value: stats.jobs_by_status?.Withdrawn ?? 0,
      icon: <Trash2 size={20} />,
    },
  ];

  for (const card of dynamicCards) {
    if (card.value > 0) cards.push(card);
  }

  // Responsive grid — 2/3/4/5/6 columns based on card count
  const gridCols = {
    2: 'grid-cols-2',
    3: 'grid-cols-2 md:grid-cols-3',
    4: 'grid-cols-2 md:grid-cols-4',
    5: 'grid-cols-2 md:grid-cols-3 lg:grid-cols-5',
    6: 'grid-cols-2 md:grid-cols-3 lg:grid-cols-6',
    7: 'grid-cols-2 md:grid-cols-4 lg:grid-cols-7',
  }[cards.length] || 'grid-cols-2 md:grid-cols-4';

  return (
    <div className={`grid ${gridCols} gap-0 border-2 border-black mb-6`}>
      {cards.map((card, i) => (
        <div key={card.key} className={i < cards.length - 1 ? 'border-r-2 border-black' : ''}>
          <KPICard
            label={card.label}
            value={card.value}
            icon={card.icon}
            deltaColor={card.deltaColor}
          />
        </div>
      ))}
    </div>
  );
}
