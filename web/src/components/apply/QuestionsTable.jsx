export function QuestionsTable({ questions, onCopy }) {
  const copy = async (q) => {
    if (!q.ai_answer) return
    try {
      await navigator.clipboard.writeText(q.ai_answer)
      onCopy({ field_name: q.label })
    } catch (e) {
      // Clipboard API can reject in iframes / lost focus / denied permissions.
      // Fire onCopy with an error flag so the consumer can surface a failure toast
      // instead of falsely claiming a successful copy.
      onCopy({ field_name: q.label, error: e?.message || 'Clipboard unavailable' })
    }
  }

  return (
    <table className="w-full font-mono text-sm">
      <thead>
        <tr className="border-b-2 border-black">
          <th className="text-left p-2">Question</th>
          <th className="text-left p-2">AI Answer</th>
          <th className="w-12"></th>
        </tr>
      </thead>
      <tbody>
        {questions.map((q) => (
          <tr key={q.id} className="border-b border-black/30">
            <td className="p-2 align-top">
              {q.label}{q.required && <span className="text-red-700"> *</span>}
            </td>
            <td className="p-2">
              {q.ai_answer
                ? q.ai_answer
                : <span className="italic text-gray-500">(no AI answer — fill manually)</span>}
            </td>
            <td className="p-2">
              <button
                type="button"
                onClick={() => copy(q)}
                disabled={!q.ai_answer}
                className="px-2 py-1 border border-black hover:bg-yellow-200 disabled:opacity-30 disabled:cursor-not-allowed"
              >
                📋 Copy
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
