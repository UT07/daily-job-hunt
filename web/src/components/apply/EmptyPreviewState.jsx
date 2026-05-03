export function EmptyPreviewState({ onRetry }) {
  return (
    <div className="p-4 border-2 border-amber-500 bg-amber-50 font-mono text-sm">
      <p className="mb-2">AI prefill not available for this posting. You'll fill the form manually.</p>
      <button type="button" onClick={onRetry} className="px-3 py-1 border border-black hover:bg-yellow-200">
        Retry preview
      </button>
    </div>
  )
}
