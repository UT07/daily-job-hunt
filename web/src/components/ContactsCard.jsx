import { useState } from 'react';

function ContactItem({ contact }) {
  const [copied, setCopied] = useState(false);

  const copy = () => {
    navigator.clipboard.writeText(contact.message).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <div className="border border-gray-100 rounded-lg p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-gray-900">{contact.role}</p>
          <p className="text-xs text-gray-500 mt-0.5">{contact.why}</p>
        </div>
        <a
          href={contact.search_url}
          target="_blank"
          rel="noopener noreferrer"
          className="shrink-0 text-xs text-blue-600 hover:text-blue-800 underline"
        >
          Search LinkedIn
        </a>
      </div>
      <div className="mt-3 bg-gray-50 rounded-lg p-3 text-sm text-gray-700 relative">
        <p className="pr-16">{contact.message}</p>
        <button
          onClick={copy}
          className="absolute top-2 right-2 text-xs px-2 py-1 rounded bg-white border border-gray-200 text-blue-600 hover:bg-blue-50 transition"
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
      <div className="animate-fade-in bg-white rounded-xl shadow-sm border border-orange-200 p-6">
        <h3 className="text-xs font-semibold text-orange-600 uppercase tracking-wider mb-2">
          LinkedIn Contacts — {company}
        </h3>
        <p className="text-sm text-gray-500">No contacts found.</p>
      </div>
    );
  }

  return (
    <div className="animate-fade-in bg-white rounded-xl shadow-sm border border-orange-200 p-6">
      <h3 className="text-xs font-semibold text-orange-600 uppercase tracking-wider mb-4">
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
