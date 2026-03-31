import Button from './ui/Button';

export default function CoverLetterCard({ data, company }) {
  const link = data.pdf_url || data.drive_url || null;

  return (
    <div className="animate-fade-in border-2 border-black shadow-brutal bg-white p-5">
      <h3 className="text-xs font-bold text-stone-500 uppercase tracking-wider font-mono mb-4">
        Cover Letter — {company}
      </h3>
      {link ? (
        <a href={link} target="_blank" rel="noopener noreferrer">
          <Button variant="secondary" size="sm">Download Cover Letter PDF</Button>
        </a>
      ) : (
        <p className="text-sm text-stone-400 font-mono">PDF generation in progress...</p>
      )}
    </div>
  );
}
