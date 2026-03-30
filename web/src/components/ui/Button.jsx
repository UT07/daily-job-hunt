const VARIANTS = {
  primary:
    'bg-black text-cream border-2 border-black shadow-brutal hover:translate-x-[2px] hover:translate-y-[2px] hover:shadow-brutal-sm active:translate-x-1 active:translate-y-1 active:shadow-none',
  secondary:
    'bg-cream text-black border-2 border-black shadow-brutal hover:bg-stone-100 hover:translate-x-[2px] hover:translate-y-[2px] hover:shadow-brutal-sm',
  accent:
    'bg-yellow text-black border-2 border-black shadow-brutal hover:bg-yellow-dark hover:translate-x-[2px] hover:translate-y-[2px] hover:shadow-brutal-sm',
  ghost:
    'bg-transparent text-stone-600 border-2 border-transparent hover:border-black hover:text-black',
  danger:
    'bg-error text-white border-2 border-black shadow-brutal hover:translate-x-[2px] hover:translate-y-[2px] hover:shadow-brutal-sm',
};

const SIZES = {
  sm: 'px-3 py-1.5 text-xs',
  md: 'px-5 py-2.5 text-sm',
  lg: 'px-6 py-3 text-base',
};

export default function Button({
  variant = 'primary',
  size = 'md',
  disabled = false,
  loading = false,
  children,
  className = '',
  ...props
}) {
  return (
    <button
      disabled={disabled || loading}
      className={`font-heading font-bold transition-all cursor-pointer
        disabled:opacity-50 disabled:cursor-not-allowed disabled:translate-x-0 disabled:translate-y-0 disabled:shadow-brutal
        inline-flex items-center justify-center gap-2
        ${VARIANTS[variant]} ${SIZES[size]} ${className}`}
      {...props}
    >
      {loading && <span className="spinner" />}
      {children}
    </button>
  );
}
