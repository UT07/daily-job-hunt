import Button from '../ui/Button';

/**
 * The compiled PDF, and an honest statement of how current it is.
 *
 * A compile takes ~15s, so this pane is out of date for most of the time the
 * user spends editing. The requirement is not freshness — it is that the user
 * can always tell. A visibly stale PDF is fine; an invisibly stale one is not.
 */
export default function PdfPane({ pdfUrl, compiling, pendingChanges, error, onRecompile }) {
  return (
    <div className="flex flex-col gap-2 h-full">
      <div className="flex items-center gap-2">
        <Button size="sm" variant="secondary" onClick={onRecompile} disabled={compiling}>
          {compiling ? 'Compiling…' : 'Recompile'}
        </Button>
        {pendingChanges > 0 && !compiling && (
          <span role="status" className="text-xs text-yellow-dark">
            {pendingChanges} {pendingChanges === 1 ? 'change' : 'changes'} not yet compiled
          </span>
        )}
      </div>

      {error && (
        <div role="alert" className="p-2 border-2 border-error bg-error-light text-xs">{error}</div>
      )}

      {pdfUrl ? (
        // Requires Content-Disposition: inline on the presigned URL. An
        // `attachment` disposition makes the browser download rather than
        // render, which blanked this preview once already.
        <iframe
          title="Resume preview"
          src={pdfUrl}
          className="w-full flex-1 min-h-[600px] border-2 border-stone-200"
        />
      ) : (
        <div className="flex-1 min-h-[600px] border-2 border-dashed border-stone-200 grid place-items-center text-sm text-stone-400">
          No PDF yet
        </div>
      )}
    </div>
  );
}
