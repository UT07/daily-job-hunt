export default function KPICard({ label, value, delta, deltaColor = 'text-success', icon }) {
  return (
    <div className="border-2 border-black bg-white p-5">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-[11px] font-bold text-stone-400 uppercase tracking-wider">
            {label}
          </p>
          <p className="text-3xl font-bold font-mono text-black mt-1 tracking-tight">
            {value}
          </p>
          {delta && (
            <p className={`text-xs font-mono font-semibold mt-1 ${deltaColor}`}>
              {delta}
            </p>
          )}
        </div>
        {icon && (
          <div className="text-stone-400">{icon}</div>
        )}
      </div>
    </div>
  );
}
