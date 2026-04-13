import { useState, useEffect } from 'react';
import { Outlet, Navigate } from 'react-router-dom';
import { useAuth } from '../auth/useAuth';
import { apiGet } from '../api';
import Sidebar from '../components/layout/Sidebar';
import MobileNav from '../components/layout/MobileNav';
import ConsentBanner from '../components/ConsentBanner';
import FinishSetupBanner from '../components/FinishSetupBanner';

export default function AppLayout() {
  const { user, loading } = useAuth();
  const [onboardingDone, setOnboardingDone] = useState(null); // null = loading
  const [profileComplete, setProfileComplete] = useState(true);

  useEffect(() => {
    if (!user) return;
    apiGet('/api/profile').then(data => {
      // Existing users with a name are treated as onboarded (backward compat)
      setOnboardingDone(!!data.onboarding_completed_at || !!data.full_name);
      // Profile is "complete" if key fields are filled
      setProfileComplete(!!(data.full_name && data.phone && data.location));
    }).catch(() => {
      setOnboardingDone(false);
    });
  }, [user]);

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

  // Still checking onboarding status
  if (onboardingDone === null) {
    return (
      <div className="min-h-screen bg-cream flex items-center justify-center">
        <span className="spinner" />
      </div>
    );
  }

  // First-time user: redirect to onboarding
  if (onboardingDone === false) {
    return <Navigate to="/onboarding" replace />;
  }

  return (
    <div className="flex min-h-screen bg-cream">
      <Sidebar />
      <div className="flex-1 flex flex-col">
        {!profileComplete && <FinishSetupBanner />}
        <main className="flex-1 p-6 pb-20 md:pb-6 overflow-auto">
          <Outlet />
        </main>
      </div>
      <MobileNav />
      <ConsentBanner />
    </div>
  );
}
