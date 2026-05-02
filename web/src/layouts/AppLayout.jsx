import { Outlet, Navigate } from 'react-router-dom';
import { useAuth } from '../auth/useAuth';
import { useUserProfile } from '../hooks/useUserProfile';
import Sidebar from '../components/layout/Sidebar';
import MobileNav from '../components/layout/MobileNav';
import ConsentBanner from '../components/ConsentBanner';
import FinishSetupBanner from '../components/FinishSetupBanner';

export default function AppLayout() {
  const { user, loading } = useAuth();
  const { profile, isLoading: profileLoading } = useUserProfile();

  if (loading || (user && profileLoading)) {
    return (
      <div className="min-h-screen bg-cream flex items-center justify-center">
        <span className="spinner" />
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  // Existing users with a name are treated as onboarded (backward compat)
  const onboardingDone = !!(profile?.onboarding_completed_at || profile?.full_name);
  // Authoritative profile-complete signal from backend (check_profile_completeness).
  // Replaces the old 3-field heuristic (full_name && phone && location) which
  // drifted from the backend's 9-field check.
  const profileComplete = !!profile?.profile_complete;

  // First-time user: redirect to onboarding
  if (!onboardingDone) {
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
