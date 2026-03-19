export default function ErrorBanner({ message }) {
  return (
    <div className="animate-fade-in bg-red-50 border border-red-200 rounded-xl p-4 text-sm text-red-700">
      {message}
    </div>
  );
}
