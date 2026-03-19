export default function CoverLetterCard({ data, company }) {
  const link = data.drive_url || null;

  return (
    <div className="animate-fade-in bg-white rounded-xl shadow-sm border border-purple-200 p-6">
      <h3 className="text-xs font-semibold text-purple-600 uppercase tracking-wider mb-4">
        Cover Letter — {company}
      </h3>
      {link ? (
        <a
          href={link}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 px-4 py-2 bg-purple-600 text-white rounded-lg text-sm font-medium hover:bg-purple-700 transition"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          Open in Google Drive
        </a>
      ) : (
        <p className="text-sm text-gray-400">Drive upload unavailable</p>
      )}
    </div>
  );
}
