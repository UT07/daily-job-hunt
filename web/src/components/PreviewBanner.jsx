/**
 * Phase B.3: visible banner on Netlify deploy previews and branch deploys.
 *
 * The banner only renders when VITE_BUILD_LABEL is "preview" or "branch"
 * (set by netlify.toml's context-specific [context.*.environment] blocks).
 * Production builds set VITE_BUILD_LABEL=production and the banner is null.
 *
 * The point: when reviewing a PR's deploy preview against the prod backend,
 * users (mostly the operator) need an obvious visual cue that they are
 * looking at unmerged code. Otherwise it's easy to think a feature has
 * already shipped to prod when it has only been previewed.
 */
const LABEL = import.meta.env.VITE_BUILD_LABEL || 'production';

export default function PreviewBanner() {
  if (LABEL === 'production') return null;
  const text = LABEL === 'preview' ? 'PREVIEW BUILD (deploy preview)' : 'BRANCH BUILD';
  return (
    <div
      role="status"
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        zIndex: 9999,
        background: '#fde68a',  // amber-200
        color: '#78350f',         // amber-900
        padding: '6px 12px',
        fontFamily: 'system-ui, sans-serif',
        fontSize: '13px',
        fontWeight: 600,
        textAlign: 'center',
        borderBottom: '2px solid #f59e0b',  // amber-500
      }}
    >
      ⚠ {text} — backend is prod, but this UI is unmerged. Don't enter
      production-critical changes here.
    </div>
  );
}
