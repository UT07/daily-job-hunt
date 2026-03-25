export default function ErrorBanner({ message }) {
  return (
    <div className="animate-fade-in bg-red-900/30 border border-red-800 rounded-lg p-4 text-sm text-red-300">
      {message}
    </div>
  );
}
