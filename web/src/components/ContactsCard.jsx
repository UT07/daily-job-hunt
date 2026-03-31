import { useState } from 'react';

function ContactItem({ contact }) {
  const [copied, setCopied] = useState(false);

  const copy = () => {
    navigator.clipboard.writeText(contact.message || '').then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  const linkUrl = contact.profile_url || contact.google_url || contact.search_url;
  const linkLabel = contact.profile_url ? 'View Profile' : contact.google_url ? 'Find on Google' : 'Search LinkedIn';

  return (
    <div className="border-2 border-black p-4 bg-white">
      <div className="flex items-start justify-between gap-3">
        <div>
          {contact.name && (
            <p className="text-sm font-bold text-black">{contact.name}</p>
          )}
          <p className={`text-sm ${contact.name ? 'text-stone-600' : 'font-bold text-black'}`}>{contact.role}</p>
          <p className="text-xs text-stone-400 mt-0.5 font-mono">{contact.why}</p>
        </div>
        {linkUrl && (
          <a
            href={linkUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="shrink-0 text-xs px-2 py-1 border-2 border-black font-bold hover:bg-yellow-light transition-colors"
          >
            {linkLabel}
          </a>
        )}
      </div>
      <div className="mt-3 bg-stone-50 border-2 border-stone-200 p-3 text-sm text-stone-600 relative">
        <p className="pr-16 font-mono text-xs leading-relaxed">{contact.message}</p>
        <button
          onClick={copy}
          className="absolute top-2 right-2 text-xs px-2 py-1 border-2 border-black font-bold
            bg-white hover:bg-yellow-light transition-colors"
        >
          {copied ? 'Copied!' : 'Copy'}
        </button>
      </div>
    </div>
  );
}

export default function ContactsCard({ data, company }) {
  if (!data.contacts?.length) {
    return (
      <div className="animate-fade-in border-2 border-black shadow-brutal bg-white p-5">
        <h3 className="text-xs font-bold text-stone-500 uppercase tracking-wider font-mono mb-2">
          LinkedIn Contacts — {company}
        </h3>
        <p className="text-sm text-stone-400">No contacts found.</p>
      </div>
    );
  }

  return (
    <div className="animate-fade-in border-2 border-black shadow-brutal bg-white p-5">
      <h3 className="text-xs font-bold text-stone-500 uppercase tracking-wider font-mono mb-4">
        LinkedIn Contacts — {company}
      </h3>
      <div className="space-y-3">
        {data.contacts.map((c, i) => (
          <ContactItem key={i} contact={c} />
        ))}
      </div>
    </div>
  );
}
