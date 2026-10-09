/**
 * Has this user finished onboarding?
 *
 * `onboarding_completed_at` is the signal: the wizard's Complete Setup is the
 * only writer (PUT /api/profile with complete_onboarding: true).
 *
 * `full_name` is NOT a signal. The wizard's step-1 résumé upload writes
 * users.name (app.py upload_resume -> profile_updates), so treating a name as
 * "onboarded" let a reload mid-wizard skip straight to the Dashboard with no
 * preferences saved and the timestamp still null.
 *
 * The one fallback, and why it is safe: the column was added on 2026-04-13
 * (20260412_onboarding_profile.sql) with NO backfill, and the backend that
 * writes it only reached production after the deploy pipeline was unblocked
 * around 2026-04-22. A user who onboarded before then has a name and a null
 * timestamp and must not be trapped in the wizard. That population is closed:
 * users.created_at is NOT NULL DEFAULT now(), so nobody created after the
 * cutoff can qualify, and every such user got a name only through the current
 * wizard, which sets the timestamp on completion. The cutoff is deliberately
 * generous (a week after the unblock); a mid-wizard user from before it is
 * treated exactly as before this change.
 */
export const LEGACY_ONBOARDING_CUTOFF = '2026-05-01T00:00:00Z';

const CUTOFF_MS = Date.parse(LEGACY_ONBOARDING_CUTOFF);

export function isOnboarded(profile) {
  if (!profile) return false;
  if (profile.onboarding_completed_at) return true;
  const created = Date.parse(profile.created_at ?? '');
  return !!profile.full_name && Number.isFinite(created) && created < CUTOFF_MS;
}
