import { Briefcase, CheckCircle, Users, TrendingUp } from 'lucide-react';
import KPICard from './ui/KPICard';

export default function StatsBar({ stats }) {
  const avgScore = Math.round(stats.avg_match_score ?? 0);

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-0 border-2 border-black mb-6">
      <div className="border-r-2 border-black last:border-r-0">
        <KPICard
          label="Total Jobs"
          value={stats.total_jobs ?? 0}
          icon={<Briefcase size={20} />}
        />
      </div>
      <div className="border-r-2 border-black last:border-r-0">
        <KPICard
          label="Applied"
          value={stats.jobs_by_status?.Applied ?? 0}
          icon={<CheckCircle size={20} />}
        />
      </div>
      <div className="border-r-2 border-black last:border-r-0">
        <KPICard
          label="Interviews"
          value={stats.jobs_by_status?.Interview ?? 0}
          icon={<Users size={20} />}
        />
      </div>
      <div>
        <KPICard
          label="Avg Score"
          value={avgScore}
          deltaColor={avgScore >= 85 ? 'text-success' : avgScore >= 60 ? 'text-yellow-dark' : 'text-error'}
          icon={<TrendingUp size={20} />}
        />
      </div>
    </div>
  );
}
