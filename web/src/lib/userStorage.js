/**
 * Browser storage that belongs to a signed-in user.
 *
 * GDPR consent used to live under one global localStorage key,
 * `gdpr_consent`, that sign-out never cleared. On a shared machine the next
 * person to sign in inherited the previous user's "consented" and never saw
 * the banner -- and the key was written even when the server never recorded
 * consent. Consent is now keyed by user id and only written after the server
 * accepted it; sign-out clears every user-scoped key.
 *
 * The Add Job draft (sessionStorage) is keyed the same way, rather than
 * deleted on every sign-out: a pasted job description is unrecoverable work
 * (see AddJob.jsx), and a session that merely EXPIRED (api.js clears it on a
 * 401) must hand the draft back after re-login. Scoping is what keeps it
 * private -- another user on the same browser reads a different key. Only an
 * explicit, user-initiated signOut() removes the current user's draft.
 */
const CONSENT_PREFIX = 'gdpr_consent:';
const LEGACY_CONSENT_KEY = 'gdpr_consent';
// The pre-scoping key. Never read: it is not attributable to any user, so it
// is removed whenever it is seen.
export const LEGACY_ADDJOB_DRAFT_KEY = 'naukribaba_addjob_draft';
const ADDJOB_DRAFT_PREFIX = `${LEGACY_ADDJOB_DRAFT_KEY}:`;

/** The Add Job draft key for this user ('anonymous' when auth is off). */
export function addJobDraftKey(userId) {
  return `${ADDJOB_DRAFT_PREFIX}${userId || 'anonymous'}`;
}

export function dropLegacyAddJobDraft() {
  try {
    sessionStorage.removeItem(LEGACY_ADDJOB_DRAFT_KEY);
  } catch { /* storage disabled */ }
}

export function consentKey(userId) {
  return `${CONSENT_PREFIX}${userId}`;
}

export function hasLocalConsent(userId) {
  if (!userId) return false;
  try {
    return localStorage.getItem(consentKey(userId)) === 'true';
  } catch {
    return false;
  }
}

export function setLocalConsent(userId) {
  if (!userId) return;
  try {
    localStorage.setItem(consentKey(userId), 'true');
  } catch { /* storage disabled: the server record is the source of truth */ }
}

export function clearLocalConsent(userId) {
  try {
    if (userId) localStorage.removeItem(consentKey(userId));
    localStorage.removeItem(LEGACY_CONSENT_KEY);
  } catch { /* storage disabled */ }
}

/**
 * Consent flags, every user's. Safe on any sign-out, including an expiry:
 * the server's gdpr_consent_at restores the flag on the next visit.
 */
export function clearConsentStorage() {
  try {
    const doomed = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && (k.startsWith(CONSENT_PREFIX) || k === LEGACY_CONSENT_KEY)) doomed.push(k);
    }
    doomed.forEach((k) => localStorage.removeItem(k));
  } catch { /* storage disabled */ }
}

/**
 * An explicit, user-initiated sign-out: consent flags plus THIS user's Add
 * Job draft (and the unattributable legacy one). Not called for a session
 * expiry, which must keep the draft.
 */
export function clearOnExplicitSignOut(userId) {
  clearConsentStorage();
  dropLegacyAddJobDraft();
  try {
    sessionStorage.removeItem(addJobDraftKey(userId));
  } catch { /* storage disabled */ }
}
