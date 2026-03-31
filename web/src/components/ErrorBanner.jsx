export default function ErrorBanner({ message }) {
  return (
    <div className="animate-fade-in bg-error-light border-2 border-error text-error p-4 text-sm font-bold">
      {message}
    </div>
  );
}
