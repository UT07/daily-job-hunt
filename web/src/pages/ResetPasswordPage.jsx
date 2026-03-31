import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/useAuth';
import Button from '../components/ui/Button';
import Input from '../components/ui/Input';

export default function ResetPasswordPage() {
  const { user, loading, updatePassword, signOut } = useAuth();
  const navigate = useNavigate();
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  if (loading) {
    return (
      <div className="min-h-screen bg-cream flex items-center justify-center">
        <span className="spinner" />
      </div>
    );
  }

  // No session means the recovery link was invalid or expired
  if (!user) {
    return (
      <div className="min-h-screen bg-cream flex flex-col justify-center">
        <div className="max-w-md w-full mx-auto px-4">
          <div className="bg-white border-2 border-black shadow-brutal p-8 text-center">
            <h2 className="text-lg font-heading font-bold text-black mb-4">
              Link expired
            </h2>
            <p className="text-sm text-stone-500 mb-6">
              This password reset link has expired or is invalid. Please request a new one.
            </p>
            <Button variant="primary" onClick={() => navigate('/login')}>
              Back to sign in
            </Button>
          </div>
        </div>
      </div>
    );
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);

    if (password !== confirm) {
      setError('Passwords do not match.');
      return;
    }

    setSubmitting(true);
    try {
      await updatePassword(password);
      setDone(true);
      // Sign out so user logs in fresh with the new password
      await signOut();
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  if (done) {
    return (
      <div className="min-h-screen bg-cream flex flex-col justify-center">
        <div className="max-w-md w-full mx-auto px-4">
          <div className="bg-white border-2 border-black shadow-brutal p-8 text-center">
            <h2 className="text-lg font-heading font-bold text-black mb-4">
              Password updated
            </h2>
            <p className="text-sm text-stone-500 mb-6">
              Your password has been changed. Sign in with your new password.
            </p>
            <Button variant="primary" onClick={() => navigate('/login')}>
              Sign in
            </Button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-cream flex flex-col justify-center">
      <div className="max-w-md w-full mx-auto px-4">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 bg-black text-yellow font-mono font-bold text-2xl border-2 border-black shadow-brutal mb-4">
            N
          </div>
          <h1 className="text-3xl font-heading font-bold text-black tracking-tight">
            NAUKRIBABA
          </h1>
        </div>

        {/* Card */}
        <div className="bg-white border-2 border-black shadow-brutal p-8">
          <h2 className="text-lg font-heading font-bold text-black mb-6 text-center">
            Set new password
          </h2>

          {error && (
            <div className="mb-4 p-3 bg-error-light border-2 border-error text-sm text-error font-medium">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <Input
              label="New password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={6}
              placeholder="At least 6 characters"
            />
            <Input
              label="Confirm password"
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              required
              minLength={6}
              placeholder="Type it again"
            />
            <Button
              type="submit"
              variant="primary"
              size="lg"
              loading={submitting}
              className="w-full"
            >
              Update password
            </Button>
          </form>
        </div>
      </div>
    </div>
  );
}
