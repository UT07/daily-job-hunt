import { useState } from 'react';
import Button from '../ui/Button';
import { applySuggestion, resolveAnchor } from './suggestions';

const LABEL = 'text-[10px] font-bold text-stone-400 uppercase tracking-wider';

const STATUS_STYLE = {
  exact: 'border-stone-200 bg-white',
  moved: 'border-stone-200 bg-white',
  stale: 'border-stone-200 bg-stone-100 opacity-60',
  applied: 'border-success bg-success-light',
};

/**
 * Per-section suggestions, each anchored to the text it was written against.
 *
 * Apply edits ONE line. It does not regenerate the resume — that is what the
 * old Regenerate button did, and why the user could never tell what changed.
 * Here the before and after are both on screen, the change is one string, and
 * undo is the same edit inverted.
 *
 * Staleness is recomputed from the `sections` prop on every render rather than
 * stored. The document is the only source of truth about whether a suggestion
 * still applies, and a cached verdict would be wrong from the first keystroke
 * after it was cached.
 *
 * Applying does NOT trigger a compile. §4 names the compile triggers — section
 * blur and the Recompile button — and a 15s compile per click would punish
 * exactly the user who accepts four suggestions in a row. The change still
 * shows up as "N changes not yet compiled" in the PDF pane and greys the score
 * strip, so nothing is silently out of date.
 */
export default function SuggestionsPanel({
  sections,
  suggestions = null,
  loading = false,
  error = null,
  onRequest = () => {},
  onApplySections = () => {},
}) {
  const [dismissed, setDismissed] = useState(() => new Set());
  // id -> the inverse edit. Held here, not in the page: it is interaction
  // state about one panel session, and it dies with the panel.
  const [undos, setUndos] = useState({});

  const visible = (suggestions || []).filter((s) => !dismissed.has(s.id));

  const apply = (suggestion) => {
    const applied = applySuggestion(sections, suggestion);
    if (!applied) return; // stale: the button is disabled, this is the belt
    setUndos((prev) => ({ ...prev, [suggestion.id]: applied.undo }));
    onApplySections(applied.sections);
  };

  const revert = (id) => {
    const reverted = applySuggestion(sections, undos[id]);
    if (!reverted) return;
    setUndos((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
    onApplySections(reverted.sections);
  };

  const dismiss = (id) => setDismissed((prev) => new Set(prev).add(id));

  const actionLabel = loading
    ? 'Analysing…'
    : (suggestions === null ? 'Suggest improvements' : 'Re-analyse');

  return (
    <section data-testid="suggestions" aria-label="Suggestions" className="p-3 border-2 border-stone-200 bg-stone-50">
      <div className="flex items-center justify-between gap-2 mb-2">
        <span className={LABEL}>Suggestions</span>
        <Button size="sm" variant="secondary" onClick={onRequest} disabled={loading}>
          {actionLabel}
        </Button>
      </div>

      {error && (
        <div role="alert" className="p-2 border-2 border-error bg-error-light text-xs">{error}</div>
      )}

      {suggestions === null && !error && (
        <p className="text-xs text-stone-500">
          Reads your draft against this job description and proposes changes to
          individual lines. Nothing is changed until you apply it.
        </p>
      )}

      {suggestions !== null && visible.length === 0 && !error && (
        <p className="text-xs text-stone-500">
          Nothing to suggest — this reads well against the job description.
        </p>
      )}

      <ul className="space-y-2">
        {visible.map((s) => {
          const undo = undos[s.id];
          const status = undo ? 'applied' : resolveAnchor(sections, s).status;
          // An undo is itself an anchored edit, so it can go stale too: if the
          // user rewrote the line after accepting it, reverting would discard
          // their words.
          const canUndo = undo ? resolveAnchor(sections, undo).status !== 'stale' : false;

          return (
            <li
              key={s.id}
              data-testid={`suggestion-${s.id}`}
              data-status={status}
              className={`p-2 border-2 text-xs ${STATUS_STYLE[status]}`}
            >
              <div className={LABEL}>{s.label}</div>

              <p className="text-stone-500 italic mt-1">“{s.anchor_text}”</p>
              {s.why && <p className="text-stone-600 mt-1">{s.why}</p>}
              <p className="font-medium mt-1">{s.replacement}</p>

              {status === 'stale' && (
                <p className="text-stone-500 mt-1">You’ve edited this since — re-analyse?</p>
              )}

              <div className="flex items-center gap-2 mt-2">
                {status === 'applied' ? (
                  <>
                    <span className="font-bold text-success-dark">Applied</span>
                    <Button size="sm" variant="secondary" onClick={() => revert(s.id)} disabled={!canUndo}>
                      Undo
                    </Button>
                    {!canUndo && <span className="text-stone-500">edited since</span>}
                  </>
                ) : (
                  <>
                    <Button size="sm" onClick={() => apply(s)} disabled={status === 'stale'}>
                      Apply
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => dismiss(s.id)}>
                      Dismiss
                    </Button>
                  </>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
