const EN_DASH = '–';

/**
 * The match score as a BAND, never a point.
 *
 * Measured 2026-09-28: the same job and resume scored 25 to 80 across 30
 * verified models, and one model at temperature=0 gave three different answers
 * to three identical consecutive calls. Temperature controls sampling; it does
 * not control mixture-of-experts routing or request batching, neither of which
 * the caller can pin.
 *
 * Printing a single integer claims a precision the measurement does not have.
 * The band collapses to a point on its own when the perspectives agree, which
 * is itself information.
 */
export default function ScoreStrip({ ats, hiringManager, techRecruiter, stale = false }) {
  const perspectives = [
    ['score-ats', 'ATS', ats],
    ['score-hm', 'HM', hiringManager],
    ['score-tr', 'TR', techRecruiter],
  ].filter(([, , v]) => typeof v === 'number');

  const values = perspectives.map(([, , v]) => v);
  const scored = values.length > 0;
  const lo = scored ? Math.min(...values) : null;
  const hi = scored ? Math.max(...values) : null;

  return (
    <section
      data-testid="score-strip"
      data-stale={stale ? 'true' : 'false'}
      aria-label="Match scores"
      className={`p-3 border-2 border-stone-200 ${stale ? 'opacity-50' : ''}`}
    >
      {!scored ? (
        <span className="text-xs text-stone-500">Not scored yet</span>
      ) : (
        <>
          <div className="flex items-baseline gap-2">
            <span className="text-[10px] font-bold text-stone-400 uppercase tracking-wider">Match</span>
            <span data-testid="score-band" className="font-mono text-lg font-bold">
              {lo === hi ? `${lo}` : `${lo}${EN_DASH}${hi}`}
            </span>
            {stale && (
              // Greyed, not spinning: the previous number is still information.
              <span className="text-[10px] text-stone-400">last compiled</span>
            )}
          </div>
          <div className="flex gap-4 mt-1 text-xs font-mono text-stone-600">
            {perspectives.map(([testid, label, value]) => (
              <span key={testid} data-testid={testid}>{label} {value}</span>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
