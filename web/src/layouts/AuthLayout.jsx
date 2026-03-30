import { Outlet, Navigate } from 'react-router-dom';
import { useAuth } from '../auth/useAuth';

export default function AuthLayout() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen bg-cream flex items-center justify-center">
        <span className="spinner" />
      </div>
    );
  }

  if (user) {
    return <Navigate to="/" replace />;
  }

  return (
    <div className="min-h-screen bg-cream flex flex-col justify-center">
      <Outlet />
    </div>
  );
}
