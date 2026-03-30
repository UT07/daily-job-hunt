export default function Card({ children, className = '', hover = false, ...props }) {
  return (
    <div
      className={`bg-white border-2 border-black shadow-brutal
        ${hover ? 'hover:translate-x-[2px] hover:translate-y-[2px] hover:shadow-brutal-sm transition-all cursor-pointer' : ''}
        ${className}`}
      {...props}
    >
      {children}
    </div>
  );
}

export function CardHeader({ children, className = '' }) {
  return (
    <div className={`px-5 py-4 border-b-2 border-black ${className}`}>
      {children}
    </div>
  );
}

export function CardBody({ children, className = '' }) {
  return <div className={`px-5 py-4 ${className}`}>{children}</div>;
}
