export default function Input({
  label,
  id,
  className = '',
  ...props
}) {
  const inputId = id || label?.toLowerCase().replace(/\s+/g, '-');
  return (
    <div>
      {label && (
        <label
          htmlFor={inputId}
          className="block text-xs font-bold text-stone-500 uppercase tracking-wider mb-1.5"
        >
          {label}
        </label>
      )}
      <input
        id={inputId}
        className={`w-full bg-white border-2 border-black px-4 py-2.5 font-body text-sm text-black
          placeholder:text-stone-400
          focus:outline-none focus:shadow-brutal-yellow
          transition-shadow ${className}`}
        {...props}
      />
    </div>
  );
}

export function Textarea({ label, id, className = '', ...props }) {
  const inputId = id || label?.toLowerCase().replace(/\s+/g, '-');
  return (
    <div>
      {label && (
        <label
          htmlFor={inputId}
          className="block text-xs font-bold text-stone-500 uppercase tracking-wider mb-1.5"
        >
          {label}
        </label>
      )}
      <textarea
        id={inputId}
        className={`w-full bg-white border-2 border-black px-4 py-3 font-body text-sm text-black
          placeholder:text-stone-400 resize-y
          focus:outline-none focus:shadow-brutal-yellow
          transition-shadow ${className}`}
        {...props}
      />
    </div>
  );
}

export function Select({ label, id, children, className = '', ...props }) {
  const inputId = id || label?.toLowerCase().replace(/\s+/g, '-');
  return (
    <div>
      {label && (
        <label
          htmlFor={inputId}
          className="block text-xs font-bold text-stone-500 uppercase tracking-wider mb-1.5"
        >
          {label}
        </label>
      )}
      <select
        id={inputId}
        className={`w-full bg-white border-2 border-black px-4 py-2.5 font-body text-sm text-black
          focus:outline-none focus:shadow-brutal-yellow
          transition-shadow cursor-pointer ${className}`}
        {...props}
      >
        {children}
      </select>
    </div>
  );
}
