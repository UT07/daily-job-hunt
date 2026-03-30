import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import AuthProvider from './auth/AuthProvider';
import AppLayout from './layouts/AppLayout';
import AuthLayout from './layouts/AuthLayout';

// Lazy-loaded pages
const Dashboard = lazy(() => import('./pages/Dashboard'));
const AddJob = lazy(() => import('./pages/AddJob'));
const JobWorkspace = lazy(() => import('./pages/JobWorkspace'));
const Settings = lazy(() => import('./pages/Settings'));
const Onboarding = lazy(() => import('./pages/Onboarding'));
const Privacy = lazy(() => import('./pages/Privacy'));
const DataExport = lazy(() => import('./pages/DataExport'));
const LoginPage = lazy(() => import('./pages/LoginPage'));

// Placeholder pages (to be built in later tasks)
function UploadResume() {
  return <div className="font-heading text-stone-400">Upload Resume — coming soon</div>;
}
function InterviewPrep() {
  return <div className="font-heading text-stone-400">Interview Prep — coming soon</div>;
}
function Analytics() {
  return <div className="font-heading text-stone-400">Analytics — coming soon</div>;
}

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
      <AuthProvider>
        <Suspense fallback={<PageLoader />}>
          <Routes>
            {/* Auth pages */}
            <Route element={<AuthLayout />}>
              <Route path="/login" element={<LoginPage />} />
              <Route path="/onboarding" element={<Onboarding />} />
            </Route>

            {/* App pages (sidebar layout) */}
            <Route element={<AppLayout />}>
              <Route index element={<Dashboard />} />
              <Route path="/jobs/:jobId" element={<JobWorkspace />} />
              <Route path="/add-job" element={<AddJob />} />
              <Route path="/upload-resume" element={<UploadResume />} />
              <Route path="/interview-prep" element={<InterviewPrep />} />
              <Route path="/analytics" element={<Analytics />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="/privacy" element={<Privacy />} />
              <Route path="/data-export" element={<DataExport />} />
            </Route>
          </Routes>
        </Suspense>
      </AuthProvider>
    </BrowserRouter>
  );
}
