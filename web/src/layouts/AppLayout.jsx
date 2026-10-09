import { Outlet, Navigate, useLocation } from 'react-router-dom';
import ErrorBoundary from '../components/ErrorBoundary';
import { useAuth } from '../auth/useAuth';
import { useUserProfile } from '../hooks/useUserProfile';
import Sidebar from '../components/layout/Sidebar';
import MobileNav from '../components/layout/MobileNav';
import ConsentBanner from '../components/ConsentBanner';
import Button from '../components/ui/Button';
import FinishSetupBanner from '../components/FinishSetupBanner';
import { isOnboarded } from '../lib/onboarding';

export default function AppLayout() {
  const { user, loading } = useAuth();
  const location = useLocation();
  const { profile, isLoading: profileLoading, error: profileError,
          refetch: refetchProfile } = useUserProfile();

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

  // onboarding_completed_at only -- a name is written by the wizard's own
  // résumé upload. See lib/onboarding.js for the narrow legacy fallback.
  const onboardingDone = isOnboarded(profile);
  // Authoritative profile-complete signal from backend (check_profile_completeness).
  // Replaces the old 3-field heuristic (full_name && phone && location) which
  // drifted from the backend's 9-field check.
  const profileComplete = !!profile?.profile_complete;

  // A FAILED PROFILE FETCH IS NOT A NEW USER. `profile` is null for both, and
  // this gate read only `onboardingDone` — so any 500, 502 or cold-start
  // timeout on /api/profile sent a fully onboarded user through the wizard,
  // silently, with their real profile intact on the server. The same bug was
  // fixed for the AUTH race on 2026-05-06 (see useUserProfile.jsx); this is
  // its sibling cause.
  if (profileError) {
    return (
      <div className="min-h-screen bg-cream flex items-center justify-center p-4">
        <div className="max-w-md w-full bg-white border-2 border-black shadow-brutal p-8 text-center">
          <h2 className="text-lg font-heading font-bold text-black mb-3">
            Could not load your profile
          </h2>
          <p className="text-sm text-stone-500 mb-6">
            Your account is fine — we just could not reach the server. Nothing
            has been lost.
          </p>
          <Button variant="primary" onClick={refetchProfile}>Try again</Button>
        </div>
      </div>
    );
  }

  // First-time user: redirect to onboarding
  if (!onboardingDone) {
    return <Navigate to="/onboarding" replace />;
  }

  return (
    <div className="flex min-h-screen bg-cream">
      <Sidebar />
      {/* min-w-0 is load-bearing. A flex item defaults to min-width:auto,
          so it refuses to shrink below its content's intrinsic width — and an
          overflow-x-auto inside it is then handed unlimited width and never
          scrolls. Measured in a 1024px viewport with the job table present:
          this child rendered 1388px without min-w-0 and 800px with it, which
          is the whole "the app doesn't fit the screen" bug. */}
      <div className="flex-1 flex flex-col min-w-0">
        {!profileComplete && <FinishSetupBanner />}
        <main className="flex-1 p-6 pb-20 md:pb-6 overflow-auto">
          {/* Inside the shell, so the nav stays usable when a page throws;
              keyed on the path so navigating away clears the error. */}
          <ErrorBoundary resetKey={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
      <MobileNav />
      <ConsentBanner />
    </div>
  );
}
