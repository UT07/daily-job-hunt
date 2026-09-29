import { Check, X } from 'lucide-react';

const MET = 'met';

/**
 * Every JD requirement, matched or missing, in the JD's own words.
 *
 * This displays data the pipeline has always produced and never shown:
 * key_matches, gaps and requirement_map are written to the jobs row on every
 * scoring run. Rendering them is the largest "the AI is visible" win per unit
 * of effort in the Studio, because it costs no new model call.
 */
export default function CoveragePanel({ keyMatches = [], gaps = [], requirementMap = [] }) {
  const hasReqs = requirementMap.length > 0;
  if (!hasReqs && keyMatches.length === 0 && gaps.length === 0) return null;

  const metCount = requirementMap.filter((r) => r.severity === MET).length;

  return (
    <section className="p-3 border-2 border-stone-200 bg-stone-50" aria-label="JD coverage">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[10px] font-bold text-stone-400 uppercase tracking-wider">JD Coverage</span>
        {hasReqs && (
          <span data-testid="coverage-count" className="font-mono text-xs font-bold">
            {metCount}/{requirementMap.length}
          </span>
        )}
      </div>

      {hasReqs ? (
        <ul className="space-y-1">
          {requirementMap.map((r, i) => {
            const met = r.severity === MET;
            return (
              <li
                key={`${r.requirement}-${i}`}
                data-testid={`req-${i}`}
                data-severity={r.severity}
                className="flex gap-2 text-xs"
              >
                {met
                  ? <Check size={14} className="shrink-0 mt-0.5 text-success" aria-label="met" />
                  : <X size={14} className="shrink-0 mt-0.5 text-error" aria-label="gap" />}
                <span>
                  <span className="font-medium">{r.requirement}</span>
                  {r.evidence && <span className="block text-stone-500">{r.evidence}</span>}
                </span>
              </li>
            );
          })}
        </ul>
      ) : (
        // Older rows predate requirement_map. Their key_matches and gaps are
        // still real coverage data, so they render rather than showing nothing.
        <div className="flex flex-wrap gap-1">
          {keyMatches.map((k) => (
            <span key={k} className="text-[10px] px-1.5 py-0.5 border-2 border-success bg-success-light">{k}</span>
          ))}
          {gaps.map((g) => (
            <span key={g} className="text-[10px] px-1.5 py-0.5 border-2 border-error bg-error-light">{g}</span>
          ))}
        </div>
      )}
    </section>
  );
}
