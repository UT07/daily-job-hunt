import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import AuthProvider from './auth/AuthProvider';
import { ProfileProvider } from './hooks/useUserProfile';
import AppLayout from './layouts/AppLayout';
import AuthLayout from './layouts/AuthLayout';
import PreviewBanner from './components/PreviewBanner';

// Lazy-loaded pages
const Dashboard = lazy(() => import('./pages/Dashboard'));
const AddJob = lazy(() => import('./pages/AddJob'));
const JobWorkspace = lazy(() => import('./pages/JobWorkspace'));
const Settings = lazy(() => import('./pages/Settings'));
const Onboarding = lazy(() => import('./pages/Onboarding'));
const Privacy = lazy(() => import('./pages/Privacy'));
const DataExport = lazy(() => import('./pages/DataExport'));
const LoginPage = lazy(() => import('./pages/LoginPage'));
const ResetPasswordPage = lazy(() => import('./pages/ResetPasswordPage'));

// P0-2: "Upload Resume" / "Interview Prep" / "Analytics" used to be inline
// `-- coming soon` placeholder pages, wired into the permanent sidebar and
// mobile nav with no visual distinction from working links (audit-dashboard.md
// P0-2). Removed rather than badged:
// - Upload Resume: fully redundant — resume upload/list/delete already lives
//   at /settings (Settings.jsx, apiUpload('/api/resumes/upload', ...)).
//   Redirect below so any old link/bookmark lands on the real feature.
// - Interview Prep: the real feature already exists, just per-job rather
//   than top-level — see PrepTab in JobWorkspace.jsx, wired to
//   GET /api/dashboard/jobs/{id}/interview-prep. No generic top-level page
//   makes sense without a job in context, so this route is dropped, not
//   redirected.
// - Analytics: verified zero backend or frontend footprint anywhere in the
//   app (GET /api/quality-stats exists but is unused by any UI). Dropped.
function PageLoader() {
  return (
    <div className="flex items-center justify-center py-20">
      <span className="spinner" />
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <PreviewBanner />
      <AuthProvider>
        {/* ProfileProvider must be inside AuthProvider — it calls useAuth() */}
        <ProfileProvider>
          <Suspense fallback={<PageLoader />}>
            <Routes>
              {/* Standalone pages — outside layouts so they don't redirect */}
              <Route path="/reset-password" element={<ResetPasswordPage />} />
              <Route path="/onboarding" element={<Onboarding />} />

              {/* Auth pages */}
              <Route element={<AuthLayout />}>
                <Route path="/login" element={<LoginPage />} />
              </Route>

              {/* App pages (sidebar layout) */}
              <Route element={<AppLayout />}>
                <Route index element={<Dashboard />} />
                <Route path="/jobs/:jobId" element={<JobWorkspace />} />
                <Route path="/add-job" element={<AddJob />} />
                {/* Redundant stub — real resume upload/management lives at /settings */}
                <Route path="/upload-resume" element={<Navigate to="/settings" replace />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="/privacy" element={<Privacy />} />
                <Route path="/data-export" element={<DataExport />} />
                {/* Catch-all for removed stubs (/interview-prep, /analytics) and any
                    other stray/old link — previously an unmatched path rendered a
                    blank page with no sidebar at all. */}
                <Route path="*" element={<Navigate to="/" replace />} />
              </Route>
            </Routes>
          </Suspense>
        </ProfileProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}
