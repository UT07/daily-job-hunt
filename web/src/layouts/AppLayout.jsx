import { Outlet, Navigate } from 'react-router-dom';
import { useAuth } from '../auth/useAuth';
import Sidebar from '../components/layout/Sidebar';
import MobileNav from '../components/layout/MobileNav';
import ConsentBanner from '../components/ConsentBanner';

export default function AppLayout() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen bg-cream flex items-center justify-center">
        <span className="spinner" />
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  return (
    <div className="flex min-h-screen bg-cream">
      <Sidebar />
      <main className="flex-1 p-6 pb-20 md:pb-6 overflow-auto">
        <Outlet />
      </main>
      <MobileNav />
      <ConsentBanner />
    </div>
  );
}
