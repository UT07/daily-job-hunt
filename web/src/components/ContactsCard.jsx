import { useState } from 'react';

function ContactItem({ contact }) {
  const [copied, setCopied] = useState(false);

  const copy = () => {
    navigator.clipboard.writeText(contact.message).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  const linkUrl = contact.profile_url || contact.google_url || contact.search_url;
  const linkLabel = contact.profile_url ? 'View Profile' : 'Search LinkedIn';

  return (
    <div className="border border-slate-600 rounded-lg p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          {contact.name && (
            <p className="text-sm font-semibold text-white">{contact.name}</p>
          )}
          <p className={`text-sm ${contact.name ? 'text-slate-300' : 'font-medium text-white'}`}>{contact.role}</p>
          <p className="text-xs text-slate-400 mt-0.5">{contact.why}</p>
        </div>
        {linkUrl && (
          <a
            href={linkUrl}
            target="_blank"
            rel="noopener noreferrer"
            className={`shrink-0 text-xs px-2 py-1 rounded transition ${
              contact.profile_url
                ? 'bg-blue-600/20 text-blue-400 hover:bg-blue-600/30 font-medium'
                : 'text-blue-400 hover:text-blue-300 underline'
            }`}
          >
            {linkLabel}
          </a>
        )}
      </div>
      <div className="mt-3 bg-slate-700/50 rounded-lg p-3 text-sm text-slate-300 relative">
        <p className="pr-16">{contact.message}</p>
        <button
          onClick={copy}
          className="absolute top-2 right-2 text-xs px-2 py-1 rounded bg-slate-600 border border-slate-500 text-blue-400 hover:bg-slate-500 transition"
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
      <div className="animate-fade-in bg-slate-800 rounded-lg border border-slate-700 p-6">
        <h3 className="text-xs font-semibold text-orange-400 uppercase tracking-wider mb-2">
          LinkedIn Contacts — {company}
        </h3>
        <p className="text-sm text-slate-400">No contacts found.</p>
      </div>
    );
  }

  return (
    <div className="animate-fade-in bg-slate-800 rounded-lg border border-slate-700 p-6">
      <h3 className="text-xs font-semibold text-orange-400 uppercase tracking-wider mb-4">
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
